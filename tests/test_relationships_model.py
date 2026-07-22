"""RelationshipStore: person records, mirrors, household, resolution."""

import json
import os

import pytest

from anima.relationships import RelationshipStore
from anima.relationships.context import AccessContext

FIXED_NOW = 1_784_000_000.0


@pytest.fixture()
def rel(tmp_path):
    r = RelationshipStore(str(tmp_path / "entity"), clock=lambda: FIXED_NOW)
    yield r
    r.close()


def test_upsert_and_get_person(rel):
    p = rel.upsert_person(
        "antonia", name="Antonia",
        aliases=["toni"], channels={"telegram": "7875060073"},
        notes="companion-agent human", trust_tier="inner",
        acl={"scope": "private", "allowed_contexts": ["direct"]})
    assert p["person_id"] == "antonia"
    assert p["trust_tier"] == "inner"
    assert p["acl"] == {"scope": "private", "allowed_contexts": ["direct"]}
    assert p["created_ts"] == FIXED_NOW

    # Partial update leaves other fields intact.
    rel.upsert_person("antonia", notes="updated")
    p2 = rel.get_person("antonia")
    assert p2["notes"] == "updated"
    assert p2["name"] == "Antonia"
    assert p2["acl"]["scope"] == "private"


def test_profile_mirrored_to_person_directory(rel):
    rel.upsert_person("alice", name="Alice")
    path = os.path.join(rel.person_dir("alice"), "profile.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        mirror = json.load(f)
    assert mirror["name"] == "Alice"
    # Mirror refreshes on household change too.
    rel.add_to_household("alice")
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["household"] is True


def test_person_dir_is_traversal_safe(rel):
    rel.upsert_person("../../evil", name="Evil")
    d = rel.person_dir("../../evil")
    assert os.path.dirname(d) == rel.rel_dir  # stays inside relationships/


def test_household_membership(rel):
    rel.upsert_person("alice")
    rel.upsert_person("bob")
    rel.add_to_household("alice")
    assert rel.is_household("alice") and not rel.is_household("bob")
    assert rel.household_members() == frozenset({"alice"})
    rel.remove_from_household("alice")
    assert rel.household_members() == frozenset()


def test_household_requires_known_person(rel):
    with pytest.raises(KeyError):
        rel.add_to_household("stranger")


def test_resolve_by_name_alias_and_handle(rel):
    rel.upsert_person("christopher", name="Christopher",
                      aliases=["chris"], channels={"telegram": "6902857843"})
    assert rel.resolve("Christopher") == "christopher"
    assert rel.resolve("CHRIS") == "christopher"
    assert rel.resolve("6902857843") == "christopher"
    assert rel.resolve("nobody") is None


def test_validation(rel):
    with pytest.raises(ValueError):
        rel.upsert_person("")
    with pytest.raises(ValueError):
        rel.upsert_person("x", trust_tier="deity")
    with pytest.raises(ValueError):
        rel.upsert_person("x", acl={"scope": "banana"})


def test_default_scope_for_declaration(rel):
    rel.upsert_person("antonia", acl={"scope": "private"})
    rel.upsert_person("bob")   # default shared
    assert rel.default_scope_for("antonia") == ("private", "antonia")
    assert rel.default_scope_for("bob") == ("shared", None)
    assert rel.default_scope_for("stranger") == ("shared", None)


def test_stats_and_persistence(tmp_path):
    root = str(tmp_path / "entity")
    with RelationshipStore(root, clock=lambda: FIXED_NOW) as r:
        r.upsert_person("alice")
        r.upsert_person("bob")
        r.add_to_household("alice")
    with RelationshipStore(root, clock=lambda: FIXED_NOW) as r2:
        assert r2.stats() == {"people": 2, "household": 1}
        assert r2.household_members() == frozenset({"alice"})


def test_access_context_constructors_and_roundtrip():
    d = AccessContext.direct("alice", channel="telegram")
    assert d.kind == "direct" and d.participants == ("alice",)
    g = AccessContext.group(["a", "b", "a"])
    assert g.participants == ("a", "b")   # deduped, order kept
    p = AccessContext.public()
    assert p.participants == ()
    s = AccessContext.system()
    assert s.kind == "system"
    back = AccessContext.from_dict(d.to_dict())
    assert back == d
