"""Phase 7 — runtime wiring: settle-time extraction inside the
dispatch cycle, graph marginalia over the Observatory wire."""

import json
import urllib.request

import pytest

from anima.runtime import RuntimeShell
from anima.runtime.senses.web_sense import WebSense

from runtime_helpers import RecordingRouter

T0 = 1_784_000_000.0


class TestShellExtraction:
    def test_settled_wake_triggers_edge_extraction(self, tmp_path):
        router = RecordingRouter([
            # the agent turn (message wake, standard tier)
            RecordingRouter.result("noted, thank you."),
            # the settle-time extraction call (reflex tier)
            RecordingRouter.result(json.dumps([
                {"src_hint": "hello there",
                 "dst_hint": "the operator",
                 "rel": "involves", "confidence": 0.9}])),
        ])
        shell = RuntimeShell(str(tmp_path / "ent"), router=router,
                             clock=lambda: T0)
        shell.start()
        try:
            shell.inject_message("chris", "hello there")
            shell.run_pending_once()
            tiers = [c[0] for c in router.calls]
            assert tiers[0] == "standard" and tiers[-1] == "reflex"
            assert shell.entity.store.graph_stats()["edges"] >= 1
        finally:
            shell.shutdown()

    def test_extraction_failure_never_kills_the_wake(self, tmp_path):
        router = RecordingRouter([
            RecordingRouter.result("still fine."),
            RuntimeError("reflex endpoint went dark"),
        ])
        shell = RuntimeShell(str(tmp_path / "ent"), router=router,
                             clock=lambda: T0)
        shell.start()
        try:
            shell.inject_message("chris", "are you ok")
            results = shell.run_pending_once()
            assert results and results[0]["ok"]
        finally:
            shell.shutdown()

    def test_extraction_can_be_disabled(self, tmp_path):
        router = RecordingRouter([
            RecordingRouter.result("quiet mode."),
        ])
        shell = RuntimeShell(str(tmp_path / "ent"), router=router,
                             clock=lambda: T0, graph_extraction=False)
        shell.start()
        try:
            shell.inject_message("chris", "hush")
            shell.run_pending_once()
            assert all(c[0] != "reflex" for c in router.calls)
        finally:
            shell.shutdown()


class TestGraphMarginalia:
    def post(self, sense, doc):
        req = urllib.request.Request(
            f"http://127.0.0.1:{sense.port}/api/message",
            data=json.dumps(doc).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    @pytest.fixture()
    def shell_and_sense(self, tmp_path):
        shell = RuntimeShell(str(tmp_path / "ent"), clock=lambda: T0)
        sense = WebSense({"port": 0, "auth": "open",
                          "bind": "127.0.0.1",
                          "operator_person": "christopher"})
        shell.add_sense("web", sense)
        shell.start()
        yield shell, sense
        shell.shutdown()

    def test_why_message_carries_graph_marginalia(self,
                                                  shell_and_sense):
        shell, sense = shell_and_sense
        store = shell.entity.store
        eid = store.add_episode(
            "decision: manifests are gzipped before upload",
            kind="decision", ts=T0)
        dn = store.node_for_memory(
            "episodic", eid, kind="decision",
            label="decision: manifests are gzipped before upload",
            ts=T0)
        inc = store.add_node(
            "event", "incident: the great manifest outage",
            "an 11GB manifest took the pipeline down", ts=T0)
        store.add_edge(dn, inc, "caused", weight=0.9, ts=T0)

        doc = self.post(sense, {"text": "why do we gzip manifests?"})
        assert doc["recall"]["mode"] == "graph"
        graph = doc["recall"]["graph"]
        assert graph and graph[0]["rel_chain"] == ["→caused"]
        assert "manifest outage" in graph[0]["label"]

    def test_plain_message_has_flat_mode_and_no_graph(
            self, shell_and_sense):
        shell, sense = shell_and_sense
        doc = self.post(sense, {"text": "hello"})
        assert doc["recall"]["mode"] == "flat"
        assert doc["recall"]["graph"] == []
