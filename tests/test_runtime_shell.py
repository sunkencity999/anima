"""RuntimeShell: pidfile lock, graceful shutdown, reply routing,
console sense, real-time loop. Offline."""

import os
import threading
import time

import pytest

from anima.memory import MemoryStore
from anima.runtime import RuntimeShell, default_registry
from anima.runtime.senses.console import ConsoleSense

from runtime_helpers import RecordingRouter, tool_call

T0 = 1_784_000_000.0


def read_lineage(root):
    path = os.path.join(root, "identity", "lineage.log")
    with open(path) as f:
        return f.read()


class TestPidLock:
    def test_second_shell_refused_while_first_lives(self, tmp_path):
        root = str(tmp_path / "entity")
        shell1 = RuntimeShell(root, clock=lambda: T0)
        shell1.start()
        try:
            shell2 = RuntimeShell(root, clock=lambda: T0)
            with pytest.raises(RuntimeError, match="already hosted"):
                shell2.start()
            shell2.entity.close()
        finally:
            shell1.shutdown()

    def test_stale_pidfile_reclaimed(self, tmp_path):
        root = str(tmp_path / "entity")
        os.makedirs(root, exist_ok=True)
        # A pid that cannot be alive (beyond pid_max on any Linux).
        with open(os.path.join(root, "runtime.pid"), "w") as f:
            f.write("99999999\n")
        shell = RuntimeShell(root, clock=lambda: T0)
        shell.start()  # must not raise
        assert shell._lock.held
        shell.shutdown()

    def test_pidfile_removed_after_shutdown(self, tmp_path):
        root = str(tmp_path / "entity")
        shell = RuntimeShell(root, clock=lambda: T0)
        shell.start()
        pidfile = os.path.join(root, "runtime.pid")
        assert os.path.exists(pidfile)
        shell.shutdown()
        assert not os.path.exists(pidfile)
        # and a new shell can start against the same root
        shell2 = RuntimeShell(root, clock=lambda: T0)
        shell2.start()
        shell2.shutdown()


class TestGracefulShutdown:
    def test_shutdown_settles_episode_and_logs_lineage(self, tmp_path):
        root = str(tmp_path / "entity")
        shell = RuntimeShell(root, clock=lambda: T0)
        shell.start()
        shell.inject_message("christopher", "remember the mission")
        shell.shutdown()

        store = MemoryStore(root)
        try:
            summaries = [e["summary"]
                         for e in store.recent_episodes(20)]
        finally:
            store.close()
        assert any("runtime shutdown (graceful)" in s for s in summaries)
        # the pending message was DRAINED before sleep, not dropped
        assert any("remember the mission" in s for s in summaries)

        lineage = read_lineage(root)
        assert "shell_start" in lineage and "shell_stop" in lineage

    def test_two_lives_visible_in_lineage(self, tmp_path):
        root = str(tmp_path / "entity")
        for _ in range(2):
            shell = RuntimeShell(root, clock=lambda: T0)
            shell.start()
            shell.shutdown()
        lineage = read_lineage(root)
        assert lineage.count("shell_start") == 2
        assert lineage.count("shell_stop") == 2


class TestReplyRouting:
    def make_reply_router(self):
        return RecordingRouter([
            RecordingRouter.result(tool_calls=[tool_call(
                "reply", {"text": "hello from the entity"})]),
            RecordingRouter.result("done"),
        ])

    def test_replies_routed_to_originating_sense(self, tmp_path):
        delivered = []

        class FakeSense:
            def deliver(self, text, wake=None):
                delivered.append(text)

        shell = RuntimeShell(str(tmp_path / "entity"),
                             router=self.make_reply_router(),
                             clock=lambda: T0)
        shell.add_sense("fake", FakeSense())
        shell.start()
        shell.inject_message("christopher", "say hi", via="fake")
        shell.run_pending_once()
        assert delivered == ["hello from the entity"]
        shell.shutdown()

    def test_broken_sense_does_not_kill_loop(self, tmp_path):
        class BrokenSense:
            def deliver(self, text, wake=None):
                raise OSError("sense unplugged")

        shell = RuntimeShell(str(tmp_path / "entity"),
                             router=self.make_reply_router(),
                             clock=lambda: T0)
        shell.add_sense("broken", BrokenSense())
        shell.start()
        shell.inject_message("christopher", "say hi", via="broken")
        results = shell.run_pending_once()  # must not raise
        assert results[0]["ok"] is True
        shell.shutdown()


class TestConsoleSense:
    def test_interactive_loop_injects_and_quits(self, tmp_path):
        lines = iter(["hello entity", "/quit"])
        outputs = []
        console = ConsoleSense(sender="christopher",
                               input_fn=lambda prompt: next(lines),
                               output_fn=outputs.append)
        shell = RuntimeShell(str(tmp_path / "entity"), clock=lambda: T0)
        shell.add_sense("console", console)
        shell.start()
        console.run_interactive(shell)
        shell.shutdown()
        store = MemoryStore(str(tmp_path / "entity"))
        try:
            summaries = [e["summary"] for e in store.recent_episodes(20)]
        finally:
            store.close()
        assert any("hello entity" in s for s in summaries)

    def test_deliver_prints(self):
        outputs = []
        console = ConsoleSense(output_fn=outputs.append)
        console.deliver("hi")
        assert outputs == ["hi"]

    def test_eof_ends_loop(self, tmp_path):
        def raise_eof(prompt):
            raise EOFError
        console = ConsoleSense(input_fn=raise_eof)
        shell = RuntimeShell(str(tmp_path / "entity"), clock=lambda: T0)
        shell.add_sense("console", console)
        shell.start()
        console.run_interactive(shell)  # returns immediately
        shell.shutdown()


class TestRunLoop:
    def test_run_processes_wakes_then_stops_gracefully(self, tmp_path):
        root = str(tmp_path / "entity")
        shell = RuntimeShell(root, tick_s=0.01)
        t = threading.Thread(target=shell.run, daemon=True)
        t.start()
        deadline = time.time() + 5
        while not shell.started and time.time() < deadline:
            time.sleep(0.01)
        shell.inject_message("christopher", "wake up")
        while shell.entity.scheduler.dispatched < 1 \
                and time.time() < deadline:
            time.sleep(0.01)
        shell.stop()
        t.join(timeout=5)
        assert not t.is_alive()
        assert shell.entity.scheduler.dispatched >= 1
        store = MemoryStore(root)
        try:
            summaries = [e["summary"] for e in store.recent_episodes(20)]
        finally:
            store.close()
        assert any("wake up" in s for s in summaries)
        assert any("runtime shutdown" in s for s in summaries)


class TestConfig:
    def test_allow_shell_from_identity_config(self, tmp_path):
        root = str(tmp_path / "entity")
        os.makedirs(os.path.join(root, "identity"), exist_ok=True)
        with open(os.path.join(root, "identity", "config.json"), "w") as f:
            f.write('{"allow_shell": true}')
        shell = RuntimeShell(root, clock=lambda: T0)
        assert shell.registry.allow_shell is True
        shell.entity.close()

    def test_shell_disabled_by_default(self, tmp_path):
        shell = RuntimeShell(str(tmp_path / "entity"), clock=lambda: T0)
        assert shell.registry.allow_shell is False
        shell.entity.close()
