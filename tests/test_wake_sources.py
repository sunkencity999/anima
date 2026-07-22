"""Timer persistence + drive pressure mechanics (Phase 2)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from anima.wake import DriveSource, TimerSource  # noqa: E402

T0 = 1_000_000.0


# ── timers ────────────────────────────────────────────────────────────

def test_timer_at_fires_once(tmp_path):
    root = str(tmp_path / "entity")
    ts = TimerSource(root)
    ts.at(T0 + 60, "check the manifest job", {"job": "manifest"}, now=T0)

    assert ts.poll(T0) == []                       # not due yet
    wakes = ts.poll(T0 + 61)
    assert len(wakes) == 1
    assert wakes[0].source == "timer"
    assert wakes[0].reason == "check the manifest job"
    assert wakes[0].payload["job"] == "manifest"
    assert ts.poll(T0 + 120) == []                 # one-shot: never again
    ts.close()


def test_timer_persists_across_restart(tmp_path):
    """Scheduled intentions survive a scheduler restart (sqlite-backed)."""
    root = str(tmp_path / "entity")
    ts1 = TimerSource(root)
    ts1.at(T0 + 100, "post-restart intention", now=T0)
    ts1.every(3600, "hourly steward pass", now=T0)
    ts1.close()                                    # simulated shutdown

    ts2 = TimerSource(root)                        # restart
    intentions = ts2.open_intentions(T0)
    assert {i["reason"] for i in intentions} == {
        "post-restart intention", "hourly steward pass"}

    wakes = ts2.poll(T0 + 101)
    assert {w.reason for w in wakes} == {"post-restart intention"}
    ts2.close()


def test_recurring_timer_catches_up_with_single_wake(tmp_path):
    """Sleeping through N periods yields ONE catch-up wake, not N."""
    ts = TimerSource(str(tmp_path / "entity"))
    ts.every(600, "10min check", now=T0)

    wakes = ts.poll(T0 + 5 * 600 + 1)              # slept through 5 periods
    assert len(wakes) == 1
    assert ts.poll(T0 + 5 * 600 + 2) == []         # fast-forwarded past now
    assert len(ts.poll(T0 + 6 * 600 + 1)) == 1     # next period fires again
    ts.close()


def test_timer_cancel(tmp_path):
    ts = TimerSource(str(tmp_path / "entity"))
    tid = ts.at(T0 + 10, "cancel me", now=T0)
    ts.cancel(tid)
    assert ts.poll(T0 + 20) == []
    assert ts.open_intentions(T0) == []
    ts.close()


# ── drives ────────────────────────────────────────────────────────────

DRIVES = {
    "curiosity": {"rate_per_hour": 0.5, "threshold": 1.0,
                  "budget": {"max_tokens": 2000, "risk_cap": "low"},
                  "description": "explore something new"},
}


def test_drive_pressure_accumulates_and_crosses_threshold(tmp_path):
    ds = DriveSource(str(tmp_path / "entity"), DRIVES)

    assert ds.poll(T0) == []                       # pressure 0 at birth
    assert ds.poll(T0 + 3600) == []                # 0.5 < 1.0
    wakes = ds.poll(T0 + 2 * 3600)                 # 1.0 >= 1.0 → wake
    assert len(wakes) == 1
    w = wakes[0]
    assert w.source == "drive"
    assert w.payload["drive"] == "curiosity"
    assert w.payload["pressure"] == pytest.approx(1.0)
    # budget comes from drive config, merged over defaults
    assert w.budget["max_tokens"] == 2000
    assert w.budget["risk_cap"] == "low"
    assert w.budget["max_actions"] == 8            # default preserved
    ds.close()


def test_drive_latches_until_satisfied(tmp_path):
    """Above threshold the drive fires ONCE, then latches; satisfy()
    resets pressure and re-arms."""
    root = str(tmp_path / "entity")
    ds = DriveSource(root, DRIVES)
    ds.poll(T0)                                    # birth: baseline at T0
    assert len(ds.poll(T0 + 3 * 3600)) == 1        # fired
    assert ds.poll(T0 + 4 * 3600) == []            # latched, no spam
    assert ds.poll(T0 + 40 * 3600) == []           # still latched

    new_pressure = ds.satisfy("curiosity", now=T0 + 40 * 3600)
    assert new_pressure == 0.0
    summary = {d["name"]: d for d in ds.pressure_summary(T0 + 40 * 3600)}
    assert summary["curiosity"]["pending"] is False

    assert ds.poll(T0 + 41 * 3600) == []           # rebuilding pressure
    assert len(ds.poll(T0 + 43 * 3600)) == 1       # crossed again → wake
    ds.close()


def test_drive_state_persists_across_restart(tmp_path):
    root = str(tmp_path / "entity")
    ds1 = DriveSource(root, DRIVES)
    ds1.poll(T0)                                   # birth: baseline at T0
    ds1.poll(T0 + 3600)                            # pressure = 0.5
    ds1.close()

    ds2 = DriveSource(root, DRIVES)                # restart
    wakes = ds2.poll(T0 + 2 * 3600)                # +0.5 → 1.0 → wake
    assert len(wakes) == 1
    assert wakes[0].payload["pressure"] == pytest.approx(1.0)
    ds2.close()


def test_drive_partial_satisfaction(tmp_path):
    ds = DriveSource(str(tmp_path / "entity"), DRIVES)
    ds.poll(T0)                                    # birth: baseline at T0
    ds.poll(T0 + 6 * 3600)                         # pressure 3.0, fired+latched
    remaining = ds.satisfy("curiosity", amount=1.5, now=T0 + 6 * 3600)
    assert remaining == pytest.approx(1.5)
    # still above threshold → stays latched (no immediate refire spam)
    assert ds.poll(T0 + 6 * 3600 + 1) == []
    ds.close()


def test_unknown_drive_satisfy_raises(tmp_path):
    ds = DriveSource(str(tmp_path / "entity"), DRIVES)
    with pytest.raises(KeyError):
        ds.satisfy("nonexistent")
    ds.close()
