"""Settle-guard enforcement: settlement is architecturally unskippable."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from anima.memory import MemoryStore  # noqa: E402
from anima.wake import MessageSource, Wake, WakeScheduler  # noqa: E402
from anima.wake.settle_guard import SettleGuard, run_settled  # noqa: E402

T0 = 1_000_000.0


def make_wake(**kw):
    defaults = dict(wake_id="wake-test-1", source="message",
                    reason="test wake", payload={}, ts=T0)
    defaults.update(kw)
    return Wake(**defaults)


def test_good_report_settles_verbatim(store):
    def handler(wake):
        return {"events": ["did the thing"],
                "learnings": ["ports move; verify before use"]}

    result = run_settled(store, handler, make_wake(), now=T0)
    assert result["ok"] is True
    assert result["error"] is None
    assert len(result["receipt"]["episode_ids"]) == 2
    assert len(result["receipt"]["queued_candidate_ids"]) == 1
    eps = store.recent_episodes(5)
    assert any("did the thing" in e["summary"] for e in eps)
    assert all(e["wake_id"] == "wake-test-1" for e in eps)


def test_none_return_still_settles(store):
    """Handler returns None → synthesized 'no report' episode is written."""
    result = run_settled(store, lambda w: None, make_wake(), now=T0)
    assert result["ok"] is True
    assert len(result["receipt"]["episode_ids"]) == 1
    ep = store.get_episode(result["receipt"]["episode_ids"][0])
    assert "completed without report" in ep["summary"]
    assert "no-report" in ep["tags"]
    assert ep["wake_id"] == "wake-test-1"


def test_non_dict_return_still_settles(store):
    result = run_settled(store, lambda w: "not a dict", make_wake(), now=T0)
    assert len(result["receipt"]["episode_ids"]) == 1
    ep = store.get_episode(result["receipt"]["episode_ids"][0])
    assert "completed without report" in ep["summary"]


def test_exception_still_settles(store):
    """Handler raises → failure episode with traceback is written."""
    def handler(wake):
        raise RuntimeError("model backend exploded")

    result = run_settled(store, handler, make_wake(), now=T0)
    assert result["ok"] is False
    assert "RuntimeError: model backend exploded" in result["error"]
    ep = store.get_episode(result["receipt"]["episode_ids"][0])
    assert "wake FAILED" in ep["summary"]
    assert "model backend exploded" in ep["summary"]
    assert "Traceback" in ep["detail"]
    assert "failure" in ep["tags"]


def test_exception_reraise_after_settlement(store):
    def handler(wake):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        run_settled(store, handler, make_wake(), now=T0, reraise=True)
    # settlement happened BEFORE the re-raise
    assert store.stats()["episodes"] == 1


def test_context_manager_body_without_report(store):
    with SettleGuard(store, make_wake(), now=T0) as guard:
        pass                                        # forgot to set report
    assert guard.receipt is not None
    ep = store.get_episode(guard.receipt["episode_ids"][0])
    assert "completed without report" in ep["summary"]


def test_context_manager_exception_settles_then_propagates(store):
    with pytest.raises(RuntimeError):
        with SettleGuard(store, make_wake(), now=T0):
            raise RuntimeError("mid-wake crash")
    assert store.stats()["episodes"] == 1
    ep = store.recent_episodes(1)[0]
    assert "mid-wake crash" in ep["summary"]


def test_scheduler_has_no_unguarded_dispatch_path(tmp_path):
    """End-to-end: even a crashing handler leaves an episodic record when
    dispatched through the scheduler."""
    store = MemoryStore(str(tmp_path / "entity"))

    def bad_handler(wake):
        raise OSError("disk on fire")

    sched = WakeScheduler(store, bad_handler, clock=lambda: T0)
    msg = MessageSource()
    sched.add_source(msg)
    msg.inject("Christopher", "please crash", ts=T0)

    results = sched.run_pending(T0)
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert store.stats()["episodes"] == 1          # settled despite crash
    assert "disk on fire" in store.recent_episodes(1)[0]["summary"]
    store.close()
