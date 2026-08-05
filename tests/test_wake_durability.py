"""Durable message wakes (the 2026-08-05 incident): a message the
entity acknowledged must survive the process that acknowledged it.

Covers the full lifecycle (pending → dispatched → settled), boot-time
replay after a simulated kill, the maybe_retry tag for wakes that died
mid-turn, the stale cap, arrival-order preservation, clean-shutdown
replaying nothing, and the boot_id the Observatory uses to keep its
typing indicator honest. Fully offline.
"""

import json
import sqlite3
import os
import urllib.request

import pytest

from anima.memory import MemoryStore
from anima.runtime import RuntimeShell
from anima.runtime.observatory import render_page
from anima.runtime.senses.web_sense import WebSense
from anima.wake.orient import orient
from anima.wake.sources import Wake

T0 = 1_784_000_000.0
TOKEN = "durability-sekrit"


def kill(shell):
    """Simulate the process dying: no drain, no settle, no lineage —
    just the pidfile gone (as it would be after a reboot / stale-pid
    reclaim) and the sqlite handles dropped."""
    shell._lock.release()
    shell.entity.close()


def wake_rows(root):
    db = sqlite3.connect(os.path.join(root, "wake", "wake.sqlite"))
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(
            "SELECT * FROM message_wakes ORDER BY created_ts, rowid")]
    finally:
        db.close()


def message_results(results):
    return [r for r in results
            if r["wake"].source == "message"]


class TestLifecycle:
    def test_inject_persists_pending_then_settles(self, tmp_path):
        root = str(tmp_path / "e")
        shell = RuntimeShell(root, clock=lambda: T0)
        shell.start()
        try:
            wake = shell.inject_message("christopher", "hold this thought")
            rows = [dict(r) for r in shell.entity.messages.db.execute(
                "SELECT * FROM message_wakes")]
            assert rows[0]["wake_id"] == wake.wake_id
            assert rows[0]["status"] == "pending"
            # the payload survives whole: context + words
            payload = json.loads(rows[0]["payload"])
            assert payload["text"] == "hold this thought"
            assert payload["access_context"]["kind"] == "direct"

            shell.run_pending_once()
            row = shell.entity.messages.db.execute(
                "SELECT status FROM message_wakes WHERE wake_id=?",
                (wake.wake_id,)).fetchone()
            assert row["status"] == "settled"
        finally:
            shell.shutdown()

    def test_coalesced_wakes_settle_with_their_survivor(self, tmp_path):
        root = str(tmp_path / "e")
        shell = RuntimeShell(root, clock=lambda: T0)
        shell.start()
        try:
            shell.inject_message("christopher", "first")
            shell.inject_message("christopher", "second")  # same key
            shell.run_pending_once()
            statuses = {r["status"] for r in wake_rows(root)}
            assert statuses == {"settled"}
        finally:
            shell.shutdown()


class TestReplay:
    def test_killed_pending_wake_replays_and_dispatches(self, tmp_path):
        root = str(tmp_path / "e")
        shell1 = RuntimeShell(root, clock=lambda: T0)
        shell1.start()
        shell1.inject_message("christopher", "the dome is beautiful")
        kill(shell1)  # died with the wake still queued

        shell2 = RuntimeShell(root, clock=lambda: T0 + 60)
        shell2.start()
        try:
            results = message_results(shell2.run_pending_once())
            assert len(results) == 1
            wake = results[0]["wake"]
            assert wake.payload["text"] == "the dome is beautiful"
            assert wake.payload["replayed"] is True
            assert "maybe_retry" not in wake.payload  # never dispatched
            assert results[0]["ok"]
            # the debt is paid and receipted
            assert wake_rows(root)[0]["status"] == "settled"
            kinds = [r["kind"] for r in shell2.entity.ledger.recent(50)]
            assert "replay" in kinds
        finally:
            shell2.shutdown()

    def test_died_mid_turn_replays_tagged_maybe_retry(self, tmp_path):
        root = str(tmp_path / "e")
        shell1 = RuntimeShell(root, clock=lambda: T0)
        shell1.start()
        shell1.inject_message("christopher", "answer me carefully")
        # pump hands the wake to the scheduler (status → dispatched)…
        shell1.entity.scheduler.pump(T0)
        assert wake_rows(root)[0]["status"] == "dispatched"
        kill(shell1)  # …and the process dies before the settle

        shell2 = RuntimeShell(root, clock=lambda: T0 + 60)
        shell2.start()
        try:
            results = message_results(shell2.run_pending_once())
            assert len(results) == 1
            assert results[0]["wake"].payload["maybe_retry"] is True
            # and the ledger receipt says so, honestly
            replays = [r for r in shell2.entity.ledger.recent(50)
                       if r["kind"] == "replay"]
            assert "possible retry" in replays[0]["detail"]
        finally:
            shell2.shutdown()

    def test_clean_shutdown_replays_nothing(self, tmp_path):
        root = str(tmp_path / "e")
        shell1 = RuntimeShell(root, clock=lambda: T0)
        shell1.start()
        shell1.inject_message("christopher", "remember the mission")
        shell1.shutdown()  # graceful: drain settles the wake
        assert wake_rows(root)[0]["status"] == "settled"

        shell2 = RuntimeShell(root, clock=lambda: T0 + 60)
        shell2.start()
        try:
            assert message_results(shell2.run_pending_once()) == []
            kinds = [r["kind"] for r in shell2.entity.ledger.recent(50)]
            assert "replay" not in kinds
            # exactly one episode carries the words — no double memory
            store = shell2.entity.store
            hits = [e for e in store.recent_episodes(50)
                    if "remember the mission" in e["summary"]]
            assert len(hits) == 1
        finally:
            shell2.shutdown()

    def test_stale_wake_skipped_not_fired(self, tmp_path):
        root = str(tmp_path / "e")
        shell1 = RuntimeShell(root, clock=lambda: T0)
        shell1.start()
        shell1.inject_message("christopher", "good morning!")
        kill(shell1)

        # 25 hours later a greeting is a ghost, not a debt
        shell2 = RuntimeShell(root, clock=lambda: T0 + 25 * 3600)
        shell2.start()
        try:
            assert message_results(shell2.run_pending_once()) == []
            assert wake_rows(root)[0]["status"] == "stale"
            skips = [r for r in shell2.entity.ledger.recent(50)
                     if r["kind"] == "replay_skipped"]
            assert len(skips) == 1 and skips[0]["outcome"] == "skipped"
        finally:
            shell2.shutdown()

    def test_replay_max_age_configurable(self, tmp_path):
        root = str(tmp_path / "e")
        os.makedirs(os.path.join(root, "identity"), exist_ok=True)
        with open(os.path.join(root, "identity", "config.json"), "w") as f:
            json.dump({"wake_replay_max_age_s": 60}, f)
        shell1 = RuntimeShell(root, clock=lambda: T0)
        shell1.start()
        shell1.inject_message("christopher", "quick one")
        kill(shell1)

        shell2 = RuntimeShell(root, clock=lambda: T0 + 120)
        shell2.start()
        try:
            assert shell2.replay_max_age_s == 60
            assert message_results(shell2.run_pending_once()) == []
            assert wake_rows(root)[0]["status"] == "stale"
        finally:
            shell2.shutdown()

    def test_replay_preserves_arrival_order(self, tmp_path):
        root = str(tmp_path / "e")
        t = [T0]
        shell1 = RuntimeShell(root, clock=lambda: t[0])
        shell1.start()
        shell1.inject_message("alice", "i was first")
        t[0] = T0 + 5
        shell1.inject_message("bob", "i was second")
        kill(shell1)

        shell2 = RuntimeShell(root, clock=lambda: T0 + 60)
        shell2.start()
        try:
            results = message_results(shell2.run_pending_once())
            senders = [r["wake"].payload["sender"] for r in results]
            assert senders == ["alice", "bob"]
        finally:
            shell2.shutdown()


class TestOrientReplayNote:
    def test_prompt_notes_replay_and_possible_retry(self, tmp_path):
        store = MemoryStore(str(tmp_path / "e"))
        try:
            wake = Wake(
                wake_id="w1", source="message",
                reason="message from a (replayed after restart)",
                payload={"sender": "a", "text": "hi",
                         "replayed": True, "maybe_retry": True},
                ts=T0)
            pack = orient(store, wake, now=T0)
            assert "REPLAYED after a runtime restart" in pack
            assert "could be a retry" in pack

            plain = Wake(wake_id="w2", source="message",
                         reason="message from a",
                         payload={"sender": "a", "text": "hi"}, ts=T0)
            assert "REPLAYED" not in orient(store, plain, now=T0)
        finally:
            store.close()


class TestBootId:
    """The Observatory's honest-dots contract: the page can always ask
    which LIFE is answering."""

    @pytest.fixture()
    def shell_and_sense(self, tmp_path):
        shell = RuntimeShell(str(tmp_path / "luna"), clock=lambda: T0)
        sense = WebSense({"port": 0, "token": TOKEN, "bind": "127.0.0.1",
                          "operator_person": "christopher",
                          "stream_poll_s": 0.05,
                          "stream_heartbeat_s": 0.1})
        shell.add_sense("web", sense)
        shell.start()
        yield shell, sense
        shell.shutdown()

    def call(self, sense, path):
        url = f"http://127.0.0.1:{sense.port}{path}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {TOKEN}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_boot_id_unique_per_shell(self, tmp_path):
        s1 = RuntimeShell(str(tmp_path / "a"), clock=lambda: T0)
        s2 = RuntimeShell(str(tmp_path / "b"), clock=lambda: T0)
        assert s1.boot_id and s2.boot_id
        assert s1.boot_id != s2.boot_id
        s1.entity.close()
        s2.entity.close()

    def test_stats_carries_boot_id(self, shell_and_sense):
        shell, sense = shell_and_sense
        status, doc = self.call(sense, "/api/stats")
        assert status == 200
        assert doc["boot_id"] == shell.boot_id

    def test_stream_says_hello_with_boot_id(self, shell_and_sense):
        shell, sense = shell_and_sense
        url = f"http://127.0.0.1:{sense.port}/api/stream"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {TOKEN}")
        buf = b""
        with urllib.request.urlopen(req, timeout=10) as resp:
            import time as _t
            deadline = _t.time() + 8
            while _t.time() < deadline and b"event: hello" not in buf:
                buf += resp.read(256)
        text = buf.decode()
        assert text.startswith("event: hello\n")  # first frame, always
        data = text.split("\n")[1]
        doc = json.loads(data[len("data: "):])
        assert doc["boot_id"] == shell.boot_id

    def test_page_carries_staleness_horizon(self, shell_and_sense):
        _, sense = shell_and_sense
        page = render_page("luna", typing_stale_s=sense.typing_stale_s)
        assert "THINK_STALE_S = 120" in page
        assert "noteBoot" in page and "boot_id" in page

    def test_typing_stale_s_configurable(self, tmp_path):
        sense = WebSense({"port": 0, "token": TOKEN,
                          "typing_stale_s": 7})
        assert sense.typing_stale_s == 7.0
        page = render_page("luna", typing_stale_s=7)
        assert "THINK_STALE_S = 7" in page
