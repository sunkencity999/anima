import json
import subprocess
import sys
import time

from anima.memory import settle, run_consolidation


WAKE_REPORT = {
    "wake_id": "wake-test-1",
    "events": [
        {"summary": "Investigated GPU swap outage",
         "detail": "scene-watch SIGKILLed vision.py mid-swap",
         "actors": ["Christopher"], "tags": ["gpu"]},
        "bare string event works",
    ],
    "decisions": ["Adopt shared swap marker for all GPU tenants"],
    "learnings": [
        {"summary": "llama-server ignores SIGTERM",
         "detail": "llama-server ignores SIGTERM; needs TimeoutStopSec drop-in",
         "tags": ["systemd"]},
    ],
    "drive_satisfactions": {"stewardship": 0.8, "craft": 0.4},
}


def test_settle_round_trip(store):
    receipt = settle(store, WAKE_REPORT)
    assert receipt["wake_id"] == "wake-test-1"
    # 2 events + 1 decision + 1 learning + 1 drive episode
    assert len(receipt["episode_ids"]) == 5
    assert len(receipt["queued_candidate_ids"]) == 1

    kinds = sorted(store.get_episode(i)["kind"] for i in receipt["episode_ids"])
    assert kinds == ["decision", "drive", "event", "event", "learning"]

    # every episode carries the wake_id
    assert all(store.get_episode(i)["wake_id"] == "wake-test-1"
               for i in receipt["episode_ids"])

    # learning landed in the consolidation queue with provenance
    pending = store.pending_candidates()
    assert len(pending) == 1
    assert "SIGTERM" in pending[0]["candidate"]
    assert len(pending[0]["episode_ids"]) == 1

    # drive episode searchable
    hits = store.search_episodes("stewardship")
    assert hits and hits[0]["kind"] == "drive"


def test_settle_generates_wake_id(store):
    receipt = settle(store, {"events": ["something happened today"]})
    assert receipt["wake_id"].startswith("wake-")


# ── dry-run consolidation (no endpoint required) ──────────────────────
def test_consolidation_promote(store):
    settle(store, {"learnings": ["dasel v3 uses put subcommand for edits"]})
    report = run_consolidation(store, dry_run=True)
    assert report["engine"] == "heuristic"
    assert report["processed"] == 1
    assert report["actions"][0]["action"] == "promote"
    beliefs = store.list_beliefs(status="active")
    assert len(beliefs) == 1
    assert beliefs[0]["provenance"]  # provenance carried through
    assert store.stats()["consolidation_pending"] == 0


def test_consolidation_confirm_existing(store):
    e = store.add_episode("first sighting")
    bid = store.add_belief("Qwen3-235B llama-server serves on port 8103",
                           provenance=[e], confidence=0.6)
    settle(store, {"learnings": ["Qwen3-235B llama-server serves on port 8103"]})
    report = run_consolidation(store, dry_run=True)
    assert report["actions"][0]["action"] == "confirm"
    b = store.get_belief(bid)
    assert b["confidence"] > 0.6
    assert len(b["provenance"]) == 2
    assert len(store.list_beliefs()) == 1  # no duplicate belief created


def test_consolidation_contradict(store):
    bid = store.add_belief("the librarian endpoint 18081 is alive and serving")
    settle(store, {"learnings": [
        "the librarian endpoint 18081 is dead, no longer serving"]})
    report = run_consolidation(store, dry_run=True)
    assert report["actions"][0]["action"] == "contradict"
    old = store.get_belief(bid)
    assert old["status"] == "contradicted"
    assert old["superseded_by"] is not None
    new = store.get_belief(old["superseded_by"])
    assert new["status"] == "active"


def test_consolidation_reject_thin(store):
    store.queue_candidate("ok then")
    report = run_consolidation(store, dry_run=True)
    assert report["actions"][0]["action"] == "reject"
    assert store.list_beliefs() == []


def test_consolidation_staleness_pass(store):
    now = time.time()
    store.add_belief("ancient fact about something", ts=now - 100 * 86400)
    report = run_consolidation(store, dry_run=True, stale_after_days=30)
    assert report["flagged_stale"] == 1


# ── CLI smoke test (end-to-end through python3 -m anima.memory) ───────
def test_cli_round_trip(tmp_path):
    import os
    root = str(tmp_path / "entity")
    env = dict(os.environ)
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = pkg_dir

    def run(*args, stdin=None):
        return subprocess.run(
            [sys.executable, "-m", "anima.memory", *args],
            input=stdin, capture_output=True, text=True, env=env, timeout=30)

    r = run("remember", "--root", root, "CLI smoke test event",
            "--tags", "cli,test")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["episode_id"] == 1

    r = run("settle", "--root", root,
            stdin=json.dumps({"learnings": ["the CLI settle path works fine"]}))
    assert r.returncode == 0, r.stderr

    r = run("consolidate", "--root", root, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["processed"] == 1

    r = run("recall", "--root", root, "CLI smoke test")
    assert r.returncode == 0, r.stderr
    assert "smoke test event" in r.stdout

    r = run("stats", "--root", root)
    stats = json.loads(r.stdout)
    assert stats["episodes"] == 2
    assert stats["beliefs"]["active"] == 1


def test_settle_owner_person_id_alias(store):
    """'owner_person_id' (the read-path column name) must work as an alias
    for 'owner' in wake-report events — a silent mismatch would create
    private rows locked away from their own owner (fail-closed but lossy)."""
    from anima.memory.settle import settle
    settle(store, {"summary": "alias check", "events": [
        {"kind": "event", "summary": "aliased owner row",
         "scope": "private", "owner_person_id": "antonia"}]})
    eps = [e for e in store.recent_episodes(10) if "aliased owner" in e["summary"]]
    assert eps and eps[0]["owner_person_id"] == "antonia"
