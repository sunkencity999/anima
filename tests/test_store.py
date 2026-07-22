import time

import pytest


# ── episodic ──────────────────────────────────────────────────────────
def test_add_and_get_episode(store):
    eid = store.add_episode(
        "Fixed the vision swap guardian", detail="rollback + telegram alert",
        kind="event", actors=["Christopher"], tags=["gpu", "vision"],
        wake_id="wake-1")
    ep = store.get_episode(eid)
    assert ep["summary"] == "Fixed the vision swap guardian"
    assert ep["actors"] == ["Christopher"]
    assert ep["tags"] == ["gpu", "vision"]
    assert ep["wake_id"] == "wake-1"
    assert ep["kind"] == "event"
    assert abs(ep["ts"] - time.time()) < 5


def test_episode_fts_search(store):
    store.add_episode("Rebuilt llama.cpp against CUDA", tags=["build"])
    store.add_episode("Watered the garden", tags=["home"])
    hits = store.search_episodes("llama CUDA")
    assert len(hits) == 1
    assert "llama.cpp" in hits[0]["summary"]


def test_recent_episodes_order(store):
    store.add_episode("old", ts=100.0)
    store.add_episode("new", ts=200.0)
    recent = store.recent_episodes(limit=2)
    assert [e["summary"] for e in recent] == ["new", "old"]


# ── semantic ──────────────────────────────────────────────────────────
def test_belief_crud_and_provenance(store):
    e1 = store.add_episode("observed port 8103 serving Qwen")
    bid = store.add_belief("Qwen3-235B serves on port 8103",
                           provenance=[e1], confidence=0.8, tags=["infra"])
    b = store.get_belief(bid)
    assert b["statement"].startswith("Qwen3-235B")
    assert b["provenance"] == [e1]
    assert b["confidence"] == 0.8
    assert b["status"] == "active"

    e2 = store.add_episode("confirmed 8103 again")
    store.confirm_belief(bid, episode_ids=[e2], confidence_bump=0.1)
    b = store.get_belief(bid)
    assert set(b["provenance"]) == {e1, e2}
    assert b["confidence"] == pytest.approx(0.9)


def test_confidence_clamped(store):
    bid = store.add_belief("x is y", confidence=0.95)
    store.confirm_belief(bid, confidence_bump=0.5)
    assert store.get_belief(bid)["confidence"] == 1.0


def test_contradict_belief(store):
    old = store.add_belief("service runs on port 18081")
    new = store.add_belief("service runs on port 8103")
    store.contradict_belief(old, superseded_by=new)
    b = store.get_belief(old)
    assert b["status"] == "contradicted"
    assert b["superseded_by"] == new
    # contradicted beliefs excluded from default search
    hits = store.search_beliefs("port 18081 service runs")
    assert old not in [h["id"] for h in hits]


def test_staleness_decay(store):
    now = time.time()
    fresh = store.add_belief("fresh fact", ts=now)
    old = store.add_belief("old fact", ts=now - 40 * 86400)
    flagged = store.flag_stale_beliefs(30, now=now)
    assert [b["id"] for b in flagged] == [old]
    assert store.get_belief(old)["status"] == "stale"
    assert store.get_belief(fresh)["status"] == "active"
    # confirming a stale belief revives it
    store.confirm_belief(old)
    assert store.get_belief(old)["status"] == "active"


# ── procedural ────────────────────────────────────────────────────────
def test_skill_telemetry(store):
    store.add_skill("vision-swap", description="swap text→VL model",
                    recipe="vision.py up; ask; reap")
    store.record_skill_outcome("vision-swap", success=True)
    store.record_skill_outcome("vision-swap", success=True)
    s = store.record_skill_outcome("vision-swap", success=False,
                                   failure_mode="SIGKILL mid-swap")
    assert s["success_count"] == 2
    assert s["failure_count"] == 1
    assert s["success_rate"] == pytest.approx(2 / 3)
    assert s["known_failure_modes"] == ["SIGKILL mid-swap"]
    assert s["last_worked"] is not None
    # duplicate failure mode not re-added
    s = store.record_skill_outcome("vision-swap", success=False,
                                   failure_mode="SIGKILL mid-swap")
    assert s["known_failure_modes"] == ["SIGKILL mid-swap"]


def test_skill_missing_raises(store):
    with pytest.raises(KeyError):
        store.record_skill_outcome("nope", success=True)


# ── queue + stats ─────────────────────────────────────────────────────
def test_queue_and_stats(store):
    store.add_episode("e")
    store.add_belief("b")
    store.add_skill("s")
    store.queue_candidate("learned something", episode_ids=[1])
    st = store.stats()
    assert st["episodes"] == 1
    assert st["beliefs"]["active"] == 1
    assert st["skills"] == 1
    assert st["consolidation_pending"] == 1
