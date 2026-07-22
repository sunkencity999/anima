"""Orient pack contents + ledger append/stats (Phase 2)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anima.wake import (  # noqa: E402
    DriveSource,
    Ledger,
    TimerSource,
    Wake,
    orient,
)

T0 = 1_000_000.0
DAY = 86400.0


def make_wake(**kw):
    defaults = dict(wake_id="wake-o-1", source="message",
                    reason="message from Christopher",
                    payload={"sender": "Christopher",
                             "text": "how is the manifest job going"},
                    ts=T0)
    defaults.update(kw)
    return Wake(**defaults)


# ── orient ────────────────────────────────────────────────────────────

def test_orient_includes_recalled_beliefs_and_episodes(store):
    eid = store.add_episode("manifest job restarted after OOM",
                            tags=["manifest"], ts=T0 - 3600)
    store.add_belief("the manifest job OOMs above 8GB input",
                     provenance=[eid], confidence=0.8, ts=T0 - 3600)

    pack = orient(store, make_wake(), now=T0)
    assert "# Wake: wake-o-1" in pack
    assert "## Trigger" in pack
    assert "manifest job OOMs above 8GB" in pack      # recalled belief
    assert "manifest job restarted after OOM" in pack  # recalled episode
    assert "sender: Christopher" in pack


def test_orient_includes_intentions_and_drive_pressure(store, tmp_path):
    root = str(tmp_path / "wake-entity")
    timers = TimerSource(root)
    timers.at(T0 + 7200, "check on the manifest job at noon", now=T0)
    timers.every(DAY, "daily stewardship pass", now=T0)
    drives = DriveSource(root, {
        "curiosity": {"rate_per_hour": 0.1, "threshold": 1.0},
        "stewardship": {"rate_per_hour": 1.0, "threshold": 2.0},
    })
    drives.poll(T0 - 3600)                            # seed state an hour ago

    pack = orient(store, make_wake(), now=T0,
                  timer_source=timers, drive_source=drives)
    assert "## Open intentions" in pack
    assert "check on the manifest job at noon" in pack
    assert "daily stewardship pass" in pack
    assert "## Drive pressure" in pack
    assert "stewardship: 1.00/2.00" in pack
    timers.close()
    drives.close()


def test_orient_minimal_runtime_without_optional_sources(store):
    pack = orient(store, make_wake(), now=T0)
    assert "## Open intentions" not in pack
    assert "## Drive pressure" not in pack
    assert "No relevant memories" in pack


def test_orient_shows_budget_and_coalesce_count(store):
    w = make_wake(budget={"max_tokens": 2000, "max_actions": 4,
                          "risk_cap": "low"})
    w.coalesce_with(make_wake(wake_id="wake-o-2",
                              payload={"text": "second line"}))
    pack = orient(store, w, now=T0)
    assert "max_tokens=2000" in pack
    assert "coalesced: 1 additional wake(s)" in pack


# ── ledger ────────────────────────────────────────────────────────────

def test_ledger_append_and_for_wake(tmp_path):
    led = Ledger(str(tmp_path / "entity"))
    led.log("w1", "tool_call", "exec: systemctl status", source="message",
            model="local-235b", tokens_in=900, tokens_out=120,
            cost_usd=0.0, ts=T0)
    led.log("w1", "reply", "answered Christopher", source="message",
            model="opus", tokens_in=2000, tokens_out=300,
            cost_usd=0.05, ts=T0 + 5)
    led.log("w2", "tool_call", "drive exploration", source="drive",
            outcome="error", ts=T0 + DAY)

    w1 = led.for_wake("w1")
    assert [a["kind"] for a in w1] == ["tool_call", "reply"]
    assert w1[0]["model"] == "local-235b"
    led.close()


def test_ledger_stats_rollups(tmp_path):
    led = Ledger(str(tmp_path / "entity"))
    led.log("w1", "tool_call", source="message", tokens_in=100,
            tokens_out=10, cost_usd=0.01, ts=T0)
    led.log("w1", "reply", source="message", tokens_in=200,
            tokens_out=20, cost_usd=0.02, ts=T0)
    led.log("w2", "tool_call", source="drive", outcome="error", ts=T0 + DAY)

    stats = led.stats()
    assert stats["totals"]["actions"] == 3
    assert stats["totals"]["wakes"] == 2
    assert stats["totals"]["tokens_in"] == 300
    assert stats["totals"]["cost_usd"] == 0.03
    assert stats["totals"]["errors"] == 1
    assert stats["per_source"] == {"message": 2, "drive": 1}
    assert stats["per_kind"] == {"tool_call": 2, "reply": 1}
    assert len(stats["per_day"]) == 2                 # two distinct days
    assert sum(stats["per_day"].values()) == 3
    led.close()


def test_ledger_bind_helper(tmp_path):
    led = Ledger(str(tmp_path / "entity"))
    wake = make_wake(wake_id="w-bound", source="timer")
    log_action = led.bind(wake, clock=lambda: T0)
    log_action("tool_call", "checked service", model="local-235b")
    entries = led.for_wake("w-bound")
    assert len(entries) == 1
    assert entries[0]["source"] == "timer"
    assert entries[0]["ts"] == T0
    led.close()
