"""ANIMA relationships — per-person models with real privacy walls.

Phase 4 (ARCHITECTURE.md §5, Build Order #4). The Esmeralda/Antonia
lesson made structural: recall queries from a shared context are
PHYSICALLY UNABLE to return private-scoped memories, because the ACL
compiles to SQL WHERE clauses applied inside the query — unauthorized
rows never leave sqlite.

Public surface:
    AccessContext        — who is present when memory is touched (context.py)
    CompiledACL          — a context's visibility, compiled to SQL (acl.py)
    compile_acl          — AccessContext (+household) → CompiledACL (acl.py)
    validate_scope       — write-time scope whitelist check (acl.py)
    default_scope_for_context — contextual auto-scoping helper (acl.py)
    KNOWN_SCOPES, DEFAULT_SCOPE
    RelationshipStore    — per-person records + household registry (model.py)
"""

from .context import AccessContext
from .acl import (
    CompiledACL,
    compile_acl,
    validate_scope,
    default_scope_for_context,
    KNOWN_SCOPES,
    DEFAULT_SCOPE,
)
from .model import RelationshipStore

__all__ = [
    "AccessContext",
    "CompiledACL",
    "compile_acl",
    "validate_scope",
    "default_scope_for_context",
    "KNOWN_SCOPES",
    "DEFAULT_SCOPE",
    "RelationshipStore",
]
