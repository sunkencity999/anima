"""The ACL enforcement core (ARCHITECTURE.md §5).

The one non-negotiable design rule: **the filter is SQL, not Python.**
`compile_acl()` turns an AccessContext (plus household membership) into
a `CompiledACL`, whose `.where(prefix)` emits a parameterized WHERE
fragment that the memory store splices into its queries. Unauthorized
rows are excluded by sqlite itself — they never cross the process
boundary into Python, so no bug in ranking, rendering, or a bad model
day can leak them.

Visibility rules (whitelist; deny by default):
    private   — only when context.kind == 'direct' AND the row's
                owner_person_id is one of the participants.
    household — only when the context has ≥1 participant and EVERY
                participant is a registered household member.
    shared    — any authenticated context (direct/group/system).
    public    — anywhere, including kind='public'.
    (anything else) — matches no whitelist branch → never visible.
                A row with scope='banana' is dark to every context.

system contexts see everything, including all private rows: the
runtime's own organs (consolidation, settle, self-audit) are the
organism's own mind. That is a design decision, documented here — the
wall is between *people*, not between the entity and itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional, Tuple

from .context import AccessContext

KNOWN_SCOPES: Tuple[str, ...] = ("private", "household", "shared", "public")
DEFAULT_SCOPE = "shared"


def validate_scope(scope: str, owner: Optional[str] = None) -> str:
    """Write-time whitelist check. Raises ValueError on unknown scope."""
    if scope not in KNOWN_SCOPES:
        raise ValueError(
            f"unknown memory scope {scope!r}; known: {KNOWN_SCOPES}")
    return scope


@dataclass(frozen=True)
class CompiledACL:
    """A context's visibility, compiled. Immutable, SQL-emitting.

    allowed_scopes  — scopes visible unconditionally in this context.
    private_owners  — owners whose private rows are visible (direct only).
    all_private     — True only for system contexts.
    """

    allowed_scopes: Tuple[str, ...]
    private_owners: Tuple[str, ...] = ()
    all_private: bool = False
    context_kind: str = ""
    context_id: str = ""

    def where(self, prefix: str = "") -> Tuple[str, List[str]]:
        """Return (sql_fragment, params). prefix qualifies column names
        for joined queries, e.g. prefix='e.' → 'e.scope'.

        The fragment whitelists; anything not matched is invisible.
        An empty ACL compiles to '0 = 1' — structurally nothing.
        """
        col_scope = f"{prefix}scope"
        col_owner = f"{prefix}owner_person_id"
        clauses: List[str] = []
        params: List[str] = []
        if self.allowed_scopes:
            ph = ",".join("?" for _ in self.allowed_scopes)
            clauses.append(f"{col_scope} IN ({ph})")
            params.extend(self.allowed_scopes)
        if self.all_private:
            clauses.append(f"{col_scope} = 'private'")
        elif self.private_owners:
            ph = ",".join("?" for _ in self.private_owners)
            clauses.append(
                f"({col_scope} = 'private' AND {col_owner} IN ({ph}))")
            params.extend(self.private_owners)
        if not clauses:
            return ("0 = 1", [])
        return ("(" + " OR ".join(clauses) + ")", params)


def compile_acl(
    context: AccessContext,
    household_members: FrozenSet[str] | Iterable[str] = frozenset(),
) -> CompiledACL:
    """AccessContext (+household membership) → CompiledACL.

    Deny by default: an unrecognized kind (should be impossible past
    AccessContext validation, but defense in depth) compiles to an ACL
    that matches nothing.
    """
    household = frozenset(household_members)
    kind = context.kind
    participants = tuple(context.participants)

    if kind == "system":
        return CompiledACL(
            allowed_scopes=("public", "shared", "household"),
            all_private=True,
            context_kind=kind, context_id=context.context_id)

    if kind == "public":
        return CompiledACL(allowed_scopes=("public",),
                           context_kind=kind, context_id=context.context_id)

    if kind in ("direct", "group"):
        scopes: List[str] = ["public", "shared"]
        # Household: every participant must be a member, and there must
        # BE participants (all() over empty is vacuously true — that
        # would grant household scope to an anonymous room; refuse).
        if participants and all(p in household for p in participants):
            scopes.append("household")
        owners = participants if kind == "direct" else ()
        return CompiledACL(
            allowed_scopes=tuple(scopes),
            private_owners=tuple(owners),
            context_kind=kind, context_id=context.context_id)

    return CompiledACL(allowed_scopes=(), context_kind=kind,
                       context_id=context.context_id)


def default_scope_for_context(
    context: Optional[AccessContext],
    sender: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Contextual auto-scoping for writes: (scope, owner_person_id).

    direct → ('private', sender): what someone tells you one-on-one is
             theirs by default. Escalating to shared is a deliberate
             act (consolidation with consent), never the default.
    group  → ('shared', None)
    public → ('public', None)
    system / None → ('shared', None) — single-user mode default.
    """
    if context is None:
        return (DEFAULT_SCOPE, None)
    if context.kind == "direct":
        owner = sender or (context.participants[0]
                           if context.participants else None)
        return ("private", owner)
    if context.kind == "public":
        return ("public", None)
    return (DEFAULT_SCOPE, None)
