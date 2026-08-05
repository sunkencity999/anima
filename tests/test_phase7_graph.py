"""Phase 7 — graph store, traversal + ACL, recall-mode decision,
settle-time extraction, gc, doctor visibility. Fully offline."""

import json

import pytest

from anima.memory import MemoryStore
from anima.memory.graph import (
    decide_recall_mode,
    graph_gc,
    recall_graph,
    render_graph_lines,
)
from anima.memory.graph_extract import (
    extract_edges_for_wake,
    parse_edge_candidates,
    resolve_hint,
)
from anima.memory.store import MAX_EDGES_PER_NODE
from anima.relationships import AccessContext
from anima.relationships.acl import compile_acl

from runtime_helpers import RecordingRouter

T0 = 1_784_000_000.0


# ── store: nodes + edges ─────────────────────────────────────────────

class TestGraphStore:
    def test_add_node_and_get(self, store):
        nid = store.add_node("decision", "ship the manifest fix",
                             "we decided to ship", ts=T0)
        node = store.get_node(nid)
        assert node["kind"] == "decision"
        assert node["label"] == "ship the manifest fix"
        assert node["weight"] == 1.0 and node["stub"] is False

    def test_unknown_kind_refused(self, store):
        with pytest.raises(ValueError, match="node kind"):
            store.add_node("banana", "x")

    def test_unknown_rel_refused(self, store):
        a = store.add_node("event", "a", ts=T0)
        b = store.add_node("event", "b", ts=T0)
        with pytest.raises(ValueError, match="edge rel"):
            store.add_edge(a, b, "likes")

    def test_self_loop_refused(self, store):
        a = store.add_node("event", "a", ts=T0)
        assert store.add_edge(a, a, "caused") is None

    def test_duplicate_edge_keeps_higher_weight(self, store):
        a = store.add_node("event", "a", ts=T0)
        b = store.add_node("event", "b", ts=T0)
        e1 = store.add_edge(a, b, "caused", weight=0.6, ts=T0)
        e2 = store.add_edge(a, b, "caused", weight=0.9, ts=T0)
        assert e1 == e2
        row = store.db.execute("SELECT weight FROM edges WHERE id=?",
                               (e1,)).fetchone()
        assert row["weight"] == 0.9

    def test_edge_cap_keeps_highest_weight(self, store):
        hub = store.add_node("person", "hub", ts=T0)
        for i in range(MAX_EDGES_PER_NODE + 5):
            other = store.add_node("event", f"ev-{i}", ts=T0)
            store.add_edge(hub, other, "involves",
                           weight=(i + 1) / 100.0, ts=T0)
        n = store.db.execute(
            "SELECT COUNT(*) FROM edges WHERE src=? OR dst=?",
            (hub, hub)).fetchone()[0]
        assert n == MAX_EDGES_PER_NODE
        # the weakest edges were the ones dropped
        min_w = store.db.execute(
            "SELECT MIN(weight) FROM edges WHERE src=?",
            (hub,)).fetchone()[0]
        assert min_w > 0.05

    def test_node_for_memory_is_idempotent(self, store):
        eid = store.add_episode("the incident", ts=T0)
        n1 = store.node_for_memory("episodic", eid, kind="event",
                                   label="the incident", ts=T0)
        n2 = store.node_for_memory("episodic", eid, kind="event",
                                   label="the incident", ts=T0)
        assert n1 == n2
        assert store.get_node_id_for_memory("episodic", eid) == n1

    def test_touch_and_demote(self, store):
        nid = store.add_node("belief", "old truth", ts=T0)
        store.touch_nodes([nid], ts=T0 + 100)
        node = store.get_node(nid)
        assert node["touch_count"] == 1
        assert node["last_touched"] == T0 + 100
        store.demote_node(nid, factor=0.5)
        assert store.get_node(nid)["weight"] == 0.5

    def test_stats_include_graph(self, store):
        store.add_node("event", "lonely", ts=T0)
        s = store.stats()
        assert s["graph"]["nodes"] == 1
        assert s["graph"]["orphans"] == 1


# ── traversal + ACL ──────────────────────────────────────────────────

def chain_fixture(store):
    """decision —caused→ constraint —caused→ incident (3 hops of
    story, 2 hops of walk)."""
    d = store.add_episode(
        "decision: manifests are always gzipped before upload",
        kind="decision", tags=["manifest"], ts=T0 - 3 * 86400)
    dn = store.node_for_memory("episodic", d, kind="decision",
                               label="decision: manifests are always "
                                     "gzipped before upload", ts=T0)
    cn = store.add_node("belief",
                        "constraint: uploads over 8GB get killed",
                        "the pipeline kills any upload over 8GB",
                        ts=T0 - 40 * 86400)
    inc = store.add_node("event",
                         "incident: the great manifest outage",
                         "an 11GB raw manifest took the pipeline down "
                         "for 6 hours", ts=T0 - 90 * 86400)
    store.add_edge(dn, cn, "caused", weight=0.9, ts=T0 - 3 * 86400)
    store.add_edge(cn, inc, "caused", weight=0.9, ts=T0 - 40 * 86400)
    return dn, cn, inc


class TestRecallGraph:
    def test_two_hop_walk_reaches_the_incident(self, store):
        dn, cn, inc = chain_fixture(store)
        out = recall_graph(store, "why do we gzip manifests",
                           now=T0)
        by_id = {o["node_id"]: o for o in out}
        assert inc in by_id, "the incident must arrive via traversal"
        assert by_id[inc]["hops"] == 2
        assert by_id[inc]["rel_chain"] == ["→caused", "→caused"]

    def test_flat_recall_does_not_surface_the_incident(self, store):
        chain_fixture(store)
        from anima.memory.recall import recall_items
        items = recall_items(store, "why do we gzip manifests", now=T0)
        text = json.dumps(items)
        assert "great manifest outage" not in text

    def test_hop_budget_one_stops_short(self, store):
        dn, cn, inc = chain_fixture(store)
        out = recall_graph(store, "why do we gzip manifests",
                           hop_budget=1, now=T0)
        ids = {o["node_id"] for o in out}
        assert cn in ids and inc not in ids

    def test_used_nodes_are_touched(self, store):
        dn, cn, inc = chain_fixture(store)
        before = store.get_node(inc)["touch_count"]
        recall_graph(store, "why do we gzip manifests", now=T0)
        assert store.get_node(inc)["touch_count"] == before + 1

    def test_node_budget_respected(self, store):
        dn, cn, inc = chain_fixture(store)
        for i in range(20):
            n = store.add_node("event", f"tangent {i}", ts=T0)
            store.add_edge(dn, n, "involves", weight=0.7, ts=T0)
        out = recall_graph(store, "why do we gzip manifests",
                           node_budget=5, now=T0)
        assert len(out) <= 5

    def test_render_lines_carry_rel_chain(self, store):
        chain_fixture(store)
        out = recall_graph(store, "why do we gzip manifests", now=T0)
        lines = render_graph_lines(out)
        assert any("→caused →caused" in ln for ln in lines)
        assert any("[event] incident" in ln for ln in lines)

    def test_readonly_walk_creates_no_nodes(self, store):
        # an episode that flat recall WILL hit, but that has no node
        store.add_episode("gzip manifests before upload", ts=T0)
        before = store.graph_stats()["nodes"]
        out = recall_graph(store, "gzip manifests", now=T0,
                           readonly=True)
        assert store.graph_stats()["nodes"] == before
        assert out == []   # nothing node-ified yet → no seeds


class TestTraversalACL:
    def test_private_node_behind_public_seed_stays_dark(self, store):
        seed = store.add_node("event", "the public seed", ts=T0,
                              scope="public")
        secret = store.add_node("belief", "alice's private grief",
                                ts=T0, scope="private", owner="alice")
        store.add_edge(seed, secret, "felt_about", weight=0.9, ts=T0)

        group = compile_acl(
            AccessContext.group(["bob", "carol"], channel="chat"))
        found = store.neighbors([seed], acl=group)
        assert found == []

    def test_owner_in_direct_context_sees_it(self, store):
        seed = store.add_node("event", "the public seed", ts=T0,
                              scope="public")
        secret = store.add_node("belief", "alice's private grief",
                                ts=T0, scope="private", owner="alice")
        store.add_edge(seed, secret, "felt_about", weight=0.9, ts=T0)

        direct = compile_acl(AccessContext.direct("alice",
                                                  channel="chat"))
        found = store.neighbors([seed], acl=direct)
        assert [f["node"]["id"] for f in found] == [secret]

    def test_full_walk_respects_acl(self, store):
        # public seed episode → private node one hop out
        eid = store.add_episode("the launchpad note", ts=T0,
                                scope="public")
        sn = store.node_for_memory("episodic", eid, kind="event",
                                   label="the launchpad note", ts=T0,
                                   scope="public")
        secret = store.add_node("belief", "hidden context",
                                ts=T0, scope="private", owner="alice")
        store.add_edge(sn, secret, "part_of", weight=0.9, ts=T0)

        ctx = AccessContext.group(["bob", "carol"], channel="chat")
        out = recall_graph(store, "launchpad note", now=T0,
                           access_context=ctx)
        labels = {o["label"] for o in out}
        assert "hidden context" not in labels


# ── recall-mode decision ─────────────────────────────────────────────

class TestRecallMode:
    def test_why_question_goes_graph(self):
        assert decide_recall_mode("why do we gzip manifests",
                                  []) == "graph"

    def test_history_cue_goes_graph(self):
        assert decide_recall_mode(
            "what led to the outage", []) == "graph"

    def test_plain_lookup_stays_flat(self):
        assert decide_recall_mode("manifest status", []) == "flat"

    def test_low_diversity_flat_results_go_graph(self):
        eps = [{"summary": "manifest job restarted after failure"}
               for _ in range(5)]
        assert decide_recall_mode("manifest job", eps) == "graph"

    def test_diverse_results_stay_flat(self):
        eps = [{"summary": s} for s in (
            "manifest job restarted", "walked the dog by the pier",
            "read a paper on sqlite", "kitchen faucet fixed",
            "planned the garden beds")]
        assert decide_recall_mode("manifest job", eps) == "flat"


# ── extraction ───────────────────────────────────────────────────────

class TestExtractionParsing:
    def test_parses_clean_array(self):
        out = parse_edge_candidates(json.dumps([
            {"src_hint": "a", "dst_hint": "b", "rel": "caused",
             "confidence": 0.8}]))
        assert len(out) == 1 and out[0]["rel"] == "caused"

    def test_parses_fenced_and_prosy_output(self):
        content = ("Sure! Here are the edges:\n```json\n"
                   '[{"src_hint": "a", "dst_hint": "b",'
                   ' "rel": "involves", "confidence": 0.7}]\n```')
        assert len(parse_edge_candidates(content)) == 1

    def test_low_confidence_dropped(self):
        out = parse_edge_candidates(json.dumps([
            {"src_hint": "a", "dst_hint": "b", "rel": "caused",
             "confidence": 0.59}]))
        assert out == []

    def test_bad_rel_dropped(self):
        out = parse_edge_candidates(json.dumps([
            {"src_hint": "a", "dst_hint": "b", "rel": "adores",
             "confidence": 0.9}]))
        assert out == []

    def test_garbage_returns_empty(self):
        assert parse_edge_candidates("no json here") == []
        assert parse_edge_candidates("") == []
        assert parse_edge_candidates('{"not": "a list"}') == []


class TestHintResolution:
    def test_exact_match_wins(self, store):
        nid = store.add_node("event", "The Great Outage", ts=T0)
        assert resolve_hint(store, "the great outage", "caused") == nid

    def test_fuzzy_match(self, store):
        nid = store.add_node("event", "the great manifest outage",
                             ts=T0)
        assert resolve_hint(store, "the great manifest outage of june",
                            "caused") == nid

    def test_unresolvable_becomes_stub_with_kind_from_rel(self, store):
        nid = resolve_hint(store, "some unknown colleague", "involves",
                           ts=T0)
        node = store.get_node(nid)
        assert node["stub"] is True and node["kind"] == "person"


def wake_report_and_receipt(store, *, summaries, wake_id="wake-x"):
    receipt = {"wake_id": wake_id, "episode_ids": []}
    for s in summaries:
        receipt["episode_ids"].append(
            store.add_episode(s, wake_id=wake_id, ts=T0))
    report = {"wake_id": wake_id, "ts": T0,
              "events": [{"summary": s} for s in summaries],
              "decisions": [], "learnings": []}
    return report, receipt


class TestExtractEdgesForWake:
    def test_edges_created_from_model_output(self, store):
        report, receipt = wake_report_and_receipt(
            store, summaries=["decided to gzip manifests",
                              "remembered the 8GB kill limit"])
        router = RecordingRouter([RecordingRouter.result(json.dumps([
            {"src_hint": "decided to gzip manifests",
             "dst_hint": "remembered the 8GB kill limit",
             "rel": "caused", "confidence": 0.85}]))])
        out = extract_edges_for_wake(store, router, report, receipt,
                                     now=T0)
        assert out["ok"] and out["edges_added"] == 1
        assert router.calls[0][0] == "reflex"
        assert store.graph_stats()["edges"] == 1

    def test_unresolvable_hint_creates_stub(self, store):
        report, receipt = wake_report_and_receipt(
            store, summaries=["talked about the launch"])
        router = RecordingRouter([RecordingRouter.result(json.dumps([
            {"src_hint": "talked about the launch",
             "dst_hint": "Dr. Nobody Anywhere",
             "rel": "involves", "confidence": 0.9}]))])
        out = extract_edges_for_wake(store, router, report, receipt,
                                     now=T0)
        assert out["stubs_created"] == 1
        assert store.graph_stats()["stubs"] == 1

    def test_supersedes_demotes_never_deletes(self, store):
        old = store.add_node("decision", "the old upload policy",
                             ts=T0)
        report, receipt = wake_report_and_receipt(
            store, summaries=["adopted the new upload policy"])
        router = RecordingRouter([RecordingRouter.result(json.dumps([
            {"src_hint": "adopted the new upload policy",
             "dst_hint": "the old upload policy",
             "rel": "supersedes", "confidence": 0.95}]))])
        extract_edges_for_wake(store, router, report, receipt, now=T0)
        node = store.get_node(old)
        assert node is not None          # never deleted
        assert node["weight"] == 0.5     # demoted

    def test_router_failure_is_non_fatal(self, store):
        report, receipt = wake_report_and_receipt(
            store, summaries=["a quiet moment"])
        router = RecordingRouter([RuntimeError("endpoint on fire")])
        out = extract_edges_for_wake(store, router, report, receipt,
                                     now=T0)
        assert out["ok"] is False
        assert "endpoint on fire" in out["error"]

    def test_model_gibberish_adds_nothing(self, store):
        report, receipt = wake_report_and_receipt(
            store, summaries=["a quiet moment"])
        router = RecordingRouter([
            RecordingRouter.result("I cannot help with that.")])
        out = extract_edges_for_wake(store, router, report, receipt,
                                     now=T0)
        assert out["ok"] and out["edges_added"] == 0
        assert store.graph_stats()["edges"] == 0

    def test_empty_receipt_skips_the_model_call(self, store):
        router = RecordingRouter([])   # any call would blow the script
        out = extract_edges_for_wake(
            store, router, {"wake_id": "w"},
            {"wake_id": "w", "episode_ids": []}, now=T0)
        assert out["ok"] and out["edges_added"] == 0


# ── gc ───────────────────────────────────────────────────────────────

class TestGraphGC:
    def test_prunes_decayed_edges(self, store):
        a = store.add_node("event", "a", ts=T0)
        b = store.add_node("event", "b", ts=T0)
        c = store.add_node("event", "c", ts=T0)
        store.add_edge(a, b, "caused", weight=0.9, ts=T0)   # fresh
        store.add_edge(a, c, "caused", weight=0.1,
                       ts=T0 - 400 * 86400)                 # ancient+weak
        report = graph_gc(store, now=T0)
        assert report["pruned_edges"] == 1
        assert report["edges"] == 1

    def test_merges_duplicate_stubs(self, store):
        a = store.add_node("event", "anchor", ts=T0)
        s1 = store.add_node("person", "dr nobody", stub=True, ts=T0)
        s2 = store.add_node("person", "Dr Nobody", stub=True, ts=T0)
        store.add_edge(a, s1, "involves", weight=0.9, ts=T0)
        store.add_edge(a, s2, "involves", weight=0.8, ts=T0)
        report = graph_gc(store, now=T0)
        assert report["merged_stubs"] == 1
        assert report["stubs"] == 1

    def test_reports_orphans(self, store):
        store.add_node("event", "islanded", ts=T0)
        report = graph_gc(store, now=T0)
        assert report["orphans"] == 1

    def test_gc_leaves_memories_untouched(self, store):
        eid = store.add_episode("the memory itself", ts=T0)
        nid = store.node_for_memory("episodic", eid, kind="event",
                                    label="the memory itself", ts=T0)
        graph_gc(store, now=T0)
        assert store.get_episode(eid) is not None
        assert store.get_node(nid) is not None


class TestGraphCLIAndDoctor:
    def test_cli_graph_gc(self, tmp_path, capsys):
        import anima.cli as cli
        root = str(tmp_path / "ent")
        with MemoryStore(root) as store:
            store.add_node("event", "x", ts=T0)
        os_ident = tmp_path / "ent" / "identity"
        os_ident.mkdir(exist_ok=True)
        (os_ident / "lineage.log").write_text("t | init | born\n")
        assert cli.main(["graph", "gc", "--root", root]) == 0
        out = capsys.readouterr().out
        assert "graph gc" in out and "1 nodes" in out

    def test_cli_graph_stats_json(self, tmp_path, capsys):
        import anima.cli as cli
        root = str(tmp_path / "ent")
        with MemoryStore(root) as store:
            store.add_node("event", "x", ts=T0)
        ident = tmp_path / "ent" / "identity"
        ident.mkdir(exist_ok=True)
        (ident / "lineage.log").write_text("t | init | born\n")
        assert cli.main(["graph", "stats", "--root", root]) == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["nodes"] == 1

    def test_doctor_shows_graph_check(self, tmp_path):
        from anima.doctor import run_doctor
        root = str(tmp_path / "ent")
        with MemoryStore(root) as store:
            store.add_node("event", "x", ts=T0)
        ident = tmp_path / "ent" / "identity"
        ident.mkdir(exist_ok=True)
        (ident / "lineage.log").write_text("t | init | born\n")
        for sub in ("senses", "relationships"):
            (tmp_path / "ent" / sub).mkdir(exist_ok=True)
        checks, _ = run_doctor(root, probe=lambda url: True, now=T0)
        graph = [c for c in checks if c["name"] == "graph"]
        assert graph and graph[0]["status"] == "PASS"
        assert "1 nodes" in graph[0]["reason"]

    def test_doctor_warns_on_orphan_heavy_graph(self, tmp_path):
        from anima.doctor import run_doctor
        root = str(tmp_path / "ent")
        with MemoryStore(root) as store:
            for i in range(30):
                store.add_node("event", f"orphan-{i}", ts=T0)
        ident = tmp_path / "ent" / "identity"
        ident.mkdir(exist_ok=True)
        (ident / "lineage.log").write_text("t | init | born\n")
        for sub in ("senses", "relationships"):
            (tmp_path / "ent" / sub).mkdir(exist_ok=True)
        checks, _ = run_doctor(root, probe=lambda url: True, now=T0)
        graph = [c for c in checks if c["name"] == "graph"]
        assert graph and graph[0]["status"] == "WARN"


# ── orient integration ───────────────────────────────────────────────

class TestOrientGraphMode:
    def make_wake(self, text):
        from anima.wake import Wake
        return Wake(wake_id="wake-g-1", source="message",
                    reason="message from chris",
                    payload={"sender": "chris", "text": text}, ts=T0)

    def test_why_wake_gets_graph_section(self, store):
        from anima.wake import orient
        chain_fixture(store)
        pack = orient(store, self.make_wake(
            "why do we gzip manifests"), now=T0)
        assert "## Graph recall" in pack
        assert "→caused →caused" in pack
        assert "great manifest outage" in pack

    def test_plain_wake_stays_flat(self, store):
        from anima.wake import orient
        chain_fixture(store)
        pack = orient(store, self.make_wake("manifest status"),
                      now=T0)
        assert "## Graph recall" not in pack

    def test_payload_can_force_graph_mode(self, store):
        from anima.wake import orient
        chain_fixture(store)
        wake = self.make_wake("manifest status")
        wake.payload["recall_mode"] = "graph"
        pack = orient(store, wake, now=T0)
        assert "## Graph recall" in pack
