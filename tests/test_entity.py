"""EntityRoot: the whole organism, end-to-end with ACL walls."""

import json
import os

import pytest

import anima
from anima.entity import EntityRoot
from anima.relationships import AccessContext


class FakeClock:
    def __init__(self, t=1_784_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds
        return self.t


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def entity(tmp_path, clock):
    e = EntityRoot(str(tmp_path / "entity"), clock=clock)
    yield e
    e.close()


def test_lazy_package_export():
    assert anima.EntityRoot is EntityRoot


def test_init_creates_organs_and_lineage(entity):
    root = entity.root
    assert os.path.exists(os.path.join(root, "memory", "memory.sqlite"))
    assert os.path.exists(os.path.join(root, "ledger", "ledger.sqlite"))
    assert os.path.exists(
        os.path.join(root, "relationships", "relationships.sqlite"))
    lineage = entity.lineage()
    assert len(lineage) == 1 and "init" in lineage[0]
    assert anima.__version__ in lineage[0]


def test_lineage_appends_on_version_change_only(tmp_path, clock):
    root = str(tmp_path / "entity")
    with EntityRoot(root, clock=clock) as e1:
        assert len(e1.lineage()) == 1
    # Same version reopen: no new entry.
    with EntityRoot(root, clock=clock) as e2:
        assert len(e2.lineage()) == 1
    # New runtime version: biographical event appended.
    with EntityRoot(root, clock=clock, runtime_version="9.9.9") as e3:
        lineage = e3.lineage()
        assert len(lineage) == 2
        assert "runtime_change" in lineage[1]
        assert "-> 9.9.9" in lineage[1]
    # And it is append-only text, one line per event.
    with open(os.path.join(root, "identity", "lineage.log")) as f:
        assert len(f.read().strip().splitlines()) == 2


def test_message_wake_settles_with_context_scope(entity, clock):
    """Direct message → wake → enforced settle → private-scoped episode."""
    entity.relationships.upsert_person("antonia", name="Antonia")
    ctx = AccessContext.direct("antonia", channel="telegram")
    results = entity.wake_message("antonia", "my secret garden plan", ctx)
    assert len(results) == 1 and results[0]["ok"]
    eid = results[0]["receipt"]["episode_ids"][0]
    ep = entity.store.get_episode(eid)
    assert ep["scope"] == "private"
    assert ep["owner_person_id"] == "antonia"
    assert "secret garden" in ep["detail"]


def test_end_to_end_wall_through_full_stack(entity, clock):
    """init → message wakes → settle → recall with correct ACL."""
    rel = entity.relationships
    rel.upsert_person("antonia")
    rel.upsert_person("christopher")

    # Antonia tells the entity something in private.
    entity.wake_message("antonia", "antonia surprise party budget 300",
                        AccessContext.direct("antonia"))
    clock.advance(60)
    # A group conversation happens.
    entity.wake_message(
        "christopher", "group chat about the party logistics",
        AccessContext.group(["christopher", "antonia"], channel="family"))
    clock.advance(60)

    # Group recall (both present): Antonia's private memory is absent.
    group_pack = entity.recall(
        "party surprise budget",
        AccessContext.group(["christopher", "antonia"]))
    assert "surprise party budget" not in group_pack
    assert "party logistics" in group_pack

    # Direct recall with Antonia: present.
    direct_pack = entity.recall("party surprise budget",
                                AccessContext.direct("antonia"))
    assert "surprise party budget" in direct_pack

    # Direct recall with Christopher alone: absent.
    chris_pack = entity.recall("party surprise budget",
                               AccessContext.direct("christopher"))
    assert "surprise party budget" not in chris_pack


def test_group_message_scoped_shared(entity):
    entity.wake_message(
        "bob", "team standup notes",
        AccessContext.group(["bob", "carol"], channel="work"))
    ep = entity.store.recent_episodes(1)[0]
    assert ep["scope"] == "shared" and ep["owner_person_id"] is None


def test_settle_passthrough_with_scopes(entity):
    receipt = entity.settle({
        "wake_id": "w-manual",
        "ts": entity.clock(),
        "scope": "household",
        "events": ["household chore rotation updated",
                   {"summary": "alice's private note", "scope": "private",
                    "owner": "alice"}],
    })
    eps = [entity.store.get_episode(i) for i in receipt["episode_ids"]]
    assert eps[0]["scope"] == "household"
    assert eps[1]["scope"] == "private" and eps[1]["owner_person_id"] == "alice"


def test_household_wall_through_entity(entity):
    rel = entity.relationships
    for p in ("alice", "bob", "carol"):
        rel.upsert_person(p)
    rel.add_to_household("alice")
    rel.add_to_household("bob")
    entity.settle({"scope": "household",
                   "events": ["household wifi password rotated"]})
    inside = entity.recall("wifi password",
                           AccessContext.group(["alice", "bob"]))
    outside = entity.recall("wifi password",
                            AccessContext.group(["alice", "carol"]))
    assert "wifi password rotated" in inside
    assert "wifi password rotated" not in outside


def test_timer_wake_and_ledger(entity, clock):
    entity.timers.at(clock() + 30, "check the manifest job", now=clock())
    clock.advance(31)
    results = entity.scheduler.run_pending(now=clock())
    assert len(results) == 1
    assert "manifest job" in results[0]["wake"].reason
    stats = entity.stats()
    assert stats["wakes_dispatched"] == 1
    assert stats["ledger_entries"] >= 2   # dispatch + settle


def test_drives_config_from_identity_file(tmp_path, clock):
    root = str(tmp_path / "entity")
    os.makedirs(os.path.join(root, "identity"))
    with open(os.path.join(root, "identity", "drives.json"), "w") as f:
        json.dump({"curiosity": {"rate_per_hour": 60.0, "threshold": 1.0}}, f)
    with EntityRoot(root, clock=clock) as e:
        assert e.drives is not None
        e.scheduler.pump(clock())          # establishes drive baseline
        clock.advance(3600)                # 60 pressure/hr × 1h ≥ threshold
        results = e.scheduler.run_pending(now=clock())
        assert any(r["wake"].source == "drive" for r in results)


def test_router_wired_from_identity_policy(tmp_path, clock):
    root = str(tmp_path / "entity")
    os.makedirs(os.path.join(root, "identity"))
    policy = {"tiers": {"standard": {"candidates": [
        {"provider": "local", "model": "m", "base_url": "http://x"}]}}}
    with open(os.path.join(root, "identity", "routing.json"), "w") as f:
        json.dump(policy, f)
    with EntityRoot(root, clock=clock) as e:
        assert e.router is not None
        assert e.router.policy.tier("standard").name == "standard"
    # And without the file, no router.
    with EntityRoot(str(tmp_path / "entity2"), clock=clock) as e2:
        assert e2.router is None


def test_custom_handler_and_stats(tmp_path, clock):
    seen = []

    def handler(wake):
        seen.append(wake.wake_id)
        return {"events": [{"summary": "custom handled", "scope": "shared"}]}

    with EntityRoot(str(tmp_path / "entity"), handler=handler,
                    clock=clock) as e:
        e.wake_message("alice", "hi")
        assert len(seen) == 1
        s = e.stats()
        assert s["memory"]["episodes"] == 1
        assert s["relationships"] == {"people": 0, "household": 0}
        assert s["runtime_version"] == anima.__version__
        assert s["lineage_entries"] == 1


def test_migration_from_v1_schema(tmp_path):
    """A pre-Phase-4 memory.sqlite (no scope columns) opens cleanly and
    legacy rows read back as shared."""
    import sqlite3
    from anima.memory.store import MemoryStore
    root = str(tmp_path / "entity")
    os.makedirs(os.path.join(root, "memory"))
    db = sqlite3.connect(os.path.join(root, "memory", "memory.sqlite"))
    db.execute(
        "CREATE TABLE episodic (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL NOT NULL, wake_id TEXT, kind TEXT NOT NULL DEFAULT 'event',"
        " actors TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL,"
        " detail TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]')")
    db.execute("INSERT INTO episodic (ts, summary) VALUES (1.0, 'old row')")
    db.commit()
    db.close()
    store = MemoryStore(root)
    try:
        ep = store.get_episode(1)
        assert ep["scope"] == "shared" and ep["owner_person_id"] is None
        # And new writes with scopes work on the migrated table.
        store.add_episode("new private", scope="private", owner="a")
    finally:
        store.close()
