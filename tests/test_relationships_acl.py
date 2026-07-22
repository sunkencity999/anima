"""Phase 4 core: the privacy wall is structural, enforced inside SQL."""

import sqlite3

import pytest

import importlib

from anima.memory.recall import recall

recall_module = importlib.import_module("anima.memory.recall")
from anima.memory.store import MemoryStore, KNOWN_SCOPES as STORE_SCOPES
from anima.relationships import AccessContext, RelationshipStore
from anima.relationships.acl import (
    CompiledACL,
    KNOWN_SCOPES,
    compile_acl,
    default_scope_for_context,
    validate_scope,
)


FIXED_NOW = 1_784_000_000.0


@pytest.fixture()
def entity(tmp_path):
    root = str(tmp_path / "entity")
    store = MemoryStore(root)
    rel = RelationshipStore(root, clock=lambda: FIXED_NOW)
    rel.upsert_person("alice", name="Alice")
    rel.upsert_person("bob", name="Bob")
    rel.upsert_person("carol", name="Carol")
    yield store, rel
    store.close()
    rel.close()


def _write_corpus(store):
    """One row per scope, all matching the query token 'project'."""
    ids = {}
    ids["private_alice"] = store.add_episode(
        "alice private project detail", scope="private", owner="alice",
        ts=FIXED_NOW)
    ids["private_bob"] = store.add_episode(
        "bob private project detail", scope="private", owner="bob",
        ts=FIXED_NOW)
    ids["household"] = store.add_episode(
        "household project logistics", scope="household", ts=FIXED_NOW)
    ids["shared"] = store.add_episode(
        "shared project status", scope="shared", ts=FIXED_NOW)
    ids["public"] = store.add_episode(
        "public project announcement", scope="public", ts=FIXED_NOW)
    return ids


def _visible_ids(store, acl):
    return {e["id"] for e in store.search_episodes("project", acl=acl)}


# ── THE CORE INVARIANT ────────────────────────────────────────────────

def test_private_row_absent_from_group_context_including_owner(entity):
    """Private memory owned by A must NOT surface in a group with A+B."""
    store, rel = entity
    ids = _write_corpus(store)
    group = AccessContext.group(["alice", "bob"])
    acl = compile_acl(group, rel.household_members())
    visible = _visible_ids(store, acl)
    assert ids["private_alice"] not in visible
    assert ids["private_bob"] not in visible
    assert ids["shared"] in visible
    assert ids["public"] in visible


def test_private_row_present_in_direct_context_with_owner(entity):
    store, rel = entity
    ids = _write_corpus(store)
    direct_alice = AccessContext.direct("alice")
    acl = compile_acl(direct_alice, rel.household_members())
    visible = _visible_ids(store, acl)
    assert ids["private_alice"] in visible
    assert ids["private_bob"] not in visible   # not her memory


def test_private_row_absent_in_direct_context_with_other_person(entity):
    store, rel = entity
    ids = _write_corpus(store)
    direct_bob = AccessContext.direct("bob")
    acl = compile_acl(direct_bob, rel.household_members())
    visible = _visible_ids(store, acl)
    assert ids["private_alice"] not in visible
    assert ids["private_bob"] in visible


def test_core_invariant_through_recall_pack(entity):
    """Same invariant through the full recall/context-pack path."""
    store, rel = entity
    _write_corpus(store)
    group_pack = recall(store, "project",
                        access_context=AccessContext.group(["alice", "bob"]),
                        relationships=rel, now=FIXED_NOW)
    assert "alice private project" not in group_pack
    assert "shared project status" in group_pack

    direct_pack = recall(store, "project",
                         access_context=AccessContext.direct("alice"),
                         relationships=rel, now=FIXED_NOW)
    assert "alice private project" in direct_pack
    assert "bob private project" not in direct_pack


# ── enforcement is IN SQL, not post-filtering ─────────────────────────

def test_acl_predicates_compiled_into_sql(entity):
    """Trace every statement sqlite executes: the scope predicate is in
    the query itself, and no post-hoc Python filtering is involved."""
    store, rel = entity
    ids = _write_corpus(store)
    traced = []
    store.db.set_trace_callback(traced.append)
    acl = compile_acl(AccessContext.group(["alice", "bob"]),
                      rel.household_members())
    rows = store.search_episodes("project", acl=acl)
    store.db.set_trace_callback(None)

    select_stmts = [s for s in traced if "FROM episodic_fts" in s]
    assert select_stmts, "expected a traced FTS SELECT"
    stmt = select_stmts[0]
    assert "e.scope IN" in stmt, "scope whitelist must be inside the SQL"
    # Returned rows already exclude private ones — nothing left to filter.
    got = {r["id"] for r in rows}
    assert ids["private_alice"] not in got and ids["private_bob"] not in got


def test_private_rows_never_leave_sqlite(entity):
    """Row-level proof: run the exact ACL-filtered SQL on a raw cursor
    and confirm the private rowid is not among fetched rows at all."""
    store, rel = entity
    ids = _write_corpus(store)
    acl = compile_acl(AccessContext.group(["alice", "bob"]),
                      rel.household_members())
    where, params = acl.where("e.")
    raw = sqlite3.connect(store.db_path)
    fetched = raw.execute(
        "SELECT e.id FROM episodic_fts"
        " JOIN episodic e ON e.id = episodic_fts.rowid"
        f" WHERE episodic_fts MATCH 'project' AND {where}",
        params,
    ).fetchall()
    raw.close()
    fetched_ids = {r[0] for r in fetched}
    assert ids["private_alice"] not in fetched_ids


# ── household scope ───────────────────────────────────────────────────

def test_household_visible_when_all_participants_household(entity):
    store, rel = entity
    ids = _write_corpus(store)
    rel.add_to_household("alice")
    rel.add_to_household("bob")
    acl = compile_acl(AccessContext.group(["alice", "bob"]),
                      rel.household_members())
    assert ids["household"] in _visible_ids(store, acl)


def test_household_hidden_when_any_participant_outside(entity):
    store, rel = entity
    ids = _write_corpus(store)
    rel.add_to_household("alice")
    rel.add_to_household("bob")
    acl = compile_acl(AccessContext.group(["alice", "bob", "carol"]),
                      rel.household_members())
    assert ids["household"] not in _visible_ids(store, acl)


def test_household_hidden_with_no_participants(entity):
    """all() over an empty set must NOT grant household scope."""
    store, rel = entity
    ids = _write_corpus(store)
    rel.add_to_household("alice")
    acl = compile_acl(AccessContext.group([]), rel.household_members())
    assert ids["household"] not in _visible_ids(store, acl)


def test_household_membership_changes_take_effect(entity):
    store, rel = entity
    ids = _write_corpus(store)
    rel.add_to_household("alice")
    ctx = AccessContext.direct("alice")
    assert ids["household"] in _visible_ids(
        store, compile_acl(ctx, rel.household_members()))
    rel.remove_from_household("alice")
    assert ids["household"] not in _visible_ids(
        store, compile_acl(ctx, rel.household_members()))


# ── public / system / deny-by-default ────────────────────────────────

def test_public_context_sees_only_public(entity):
    store, rel = entity
    ids = _write_corpus(store)
    acl = compile_acl(AccessContext.public(), rel.household_members())
    assert _visible_ids(store, acl) == {ids["public"]}


def test_system_context_sees_everything(entity):
    store, rel = entity
    ids = _write_corpus(store)
    acl = compile_acl(AccessContext.system(), rel.household_members())
    assert _visible_ids(store, acl) == set(ids.values())


def test_unknown_scope_rows_invisible_to_every_context(entity):
    """Deny by default: a row with a scope outside the whitelist (e.g.
    written by a future/buggy version) is dark to all person contexts."""
    store, rel = entity
    # Bypass write-time validation to simulate corruption/drift.
    store.db.execute(
        "INSERT INTO episodic (ts, kind, summary, scope)"
        " VALUES (?, 'event', 'weird scope project row', 'banana')",
        (FIXED_NOW,))
    store.db.commit()
    for ctx in (AccessContext.direct("alice"),
                AccessContext.group(["alice", "bob"]),
                AccessContext.public()):
        acl = compile_acl(ctx, rel.household_members())
        assert not any("weird scope" in e["summary"]
                       for e in store.search_episodes("project", acl=acl))


def test_write_time_unknown_scope_rejected(entity):
    store, _ = entity
    with pytest.raises(ValueError):
        store.add_episode("x", scope="secretish")
    with pytest.raises(ValueError):
        store.add_belief("x", scope="banana")
    with pytest.raises(ValueError):
        store.add_skill("s", scope="banana")


def test_unknown_context_kind_rejected_and_denied():
    with pytest.raises(ValueError):
        AccessContext(context_id="c", kind="martian")
    # Defense in depth: even if a bogus kind reached the compiler, it
    # compiles to structurally-nothing.
    bogus = CompiledACL(allowed_scopes=())
    sql, params = bogus.where()
    assert sql == "0 = 1" and params == []


# ── ACL applies to semantic + procedural layers too ───────────────────

def test_acl_applies_to_beliefs_and_skills(entity):
    store, rel = entity
    store.add_belief("alice prefers oat milk", scope="private",
                     owner="alice", ts=FIXED_NOW)
    store.add_belief("the project deadline is friday", scope="shared",
                     ts=FIXED_NOW)
    store.add_skill("alice-birthday-plan", description="private plan for alice",
                    scope="private", owner="alice", ts=FIXED_NOW)
    store.add_skill("deploy", description="deploy the project", ts=FIXED_NOW)

    group_acl = compile_acl(AccessContext.group(["alice", "bob"]),
                            rel.household_members())
    beliefs = store.search_beliefs("alice project milk friday", acl=group_acl)
    assert all(b["scope"] != "private" for b in beliefs)
    skills = store.list_skills(acl=group_acl)
    assert [s["name"] for s in skills] == ["deploy"]

    direct_acl = compile_acl(AccessContext.direct("alice"),
                             rel.household_members())
    assert any(b["statement"].startswith("alice prefers")
               for b in store.search_beliefs("oat milk", acl=direct_acl))
    assert {s["name"] for s in store.list_skills(acl=direct_acl)} == {
        "alice-birthday-plan", "deploy"}


# ── misc: helpers, warning, scope-constant sync ───────────────────────

def test_default_scope_for_context():
    assert default_scope_for_context(
        AccessContext.direct("alice"), "alice") == ("private", "alice")
    assert default_scope_for_context(
        AccessContext.group(["a", "b"])) == ("shared", None)
    assert default_scope_for_context(
        AccessContext.public()) == ("public", None)
    assert default_scope_for_context(None) == ("shared", None)


def test_validate_scope():
    for s in KNOWN_SCOPES:
        assert validate_scope(s) == s
    with pytest.raises(ValueError):
        validate_scope("banana")


def test_scope_constants_stay_in_sync():
    assert tuple(STORE_SCOPES) == tuple(KNOWN_SCOPES)


def test_aclless_recall_warns_once(entity, recwarn):
    store, _ = entity
    _write_corpus(store)
    recall_module._ACLLESS_WARNED = False
    with pytest.warns(UserWarning, match="ACL-less recall"):
        recall(store, "project", now=FIXED_NOW)
    # Second call: no new warning.
    before = len(recwarn.list)
    recall(store, "project", now=FIXED_NOW)
    assert len(recwarn.list) == before
