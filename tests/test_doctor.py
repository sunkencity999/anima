"""anima doctor — pass, warn, and fail paths, all in tmp roots.

The endpoint probe is injected so nothing here touches the network,
and no test goes anywhere near a real entity.
"""

import json
import os
import socket
import time

import pytest

from anima.cli import main
from anima.doctor import FAIL, PASS, WARN, run_doctor


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "ent"
    assert main(["init", str(root)]) == 0
    return root


def _by_name(checks):
    return {c["name"]: c for c in checks}


def _statuses(checks):
    return {c["status"] for c in checks}


UP = lambda url: True      # noqa: E731 — reads better inline
DOWN = lambda url: False   # noqa: E731


# ── pass path ─────────────────────────────────────────────────────────

def test_fresh_root_with_reachable_endpoint_has_no_fail(root, tmp_path):
    # give it a recent backup so even that check passes
    assert main(["backup", "--root", str(root),
                 "--dest", str(tmp_path / "anima-backups" / "ent")]) == 0
    checks, code = run_doctor(str(root), probe=UP)
    assert code == 0
    assert FAIL not in _statuses(checks)
    named = _by_name(checks)
    assert named["structure"]["status"] == PASS
    assert named["identity/drives.json"]["status"] == PASS
    assert named["identity/routing.json"]["status"] == PASS
    assert named["backups"]["status"] == PASS
    assert named["pidlock"]["status"] == PASS


def test_live_pidlock_is_pass_not_warn(root):
    (root / "runtime.pid").write_text(f"{os.getpid()}\n")
    # fetch_status injected: a live pidlock makes the PWA checks want
    # to probe the Observatory, and tests never touch the network
    checks, code = run_doctor(str(root), probe=UP,
                              fetch_status=lambda url: 200)
    assert code == 0
    assert "LIVE" in _by_name(checks)["pidlock"]["reason"]


# ── warn paths ────────────────────────────────────────────────────────

def test_unreachable_endpoint_is_warn_not_fail(root):
    checks, code = run_doctor(str(root), probe=DOWN)
    assert code == 0  # warnings never fail the preflight
    endpoint = [c for c in checks if c["name"].startswith("endpoint ")]
    assert endpoint and all(c["status"] == WARN for c in endpoint)


def test_port_in_use_is_warn_likely_already_running(root):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    cfg = json.loads((root / "senses" / "http.json").read_text())
    cfg["port"] = port
    (root / "senses" / "http.json").write_text(json.dumps(cfg))
    try:
        checks, code = run_doctor(str(root), probe=UP)
    finally:
        srv.close()
    assert code == 0
    http = _by_name(checks)["senses/http.json"]
    assert http["status"] == WARN
    assert "likely already running" in http["reason"]


def test_no_backup_is_warn(root):
    checks, code = run_doctor(str(root), probe=UP)
    assert code == 0
    assert _by_name(checks)["backups"]["status"] == WARN


def test_old_backup_is_warn(root, tmp_path):
    dest = tmp_path / "anima-backups" / "ent"
    assert main(["backup", "--root", str(root), "--dest", str(dest)]) == 0
    checks, code = run_doctor(str(root), probe=UP,
                              now=time.time() + 9 * 86400)
    assert code == 0
    backups = _by_name(checks)["backups"]
    assert backups["status"] == WARN
    assert "days old" in backups["reason"]


def test_stale_pidfile_is_warn(root):
    (root / "runtime.pid").write_text("999999999\n")
    checks, code = run_doctor(str(root), probe=UP)
    assert code == 0
    assert _by_name(checks)["pidlock"]["status"] == WARN


# ── fail paths ────────────────────────────────────────────────────────

def test_missing_root_is_single_fail(tmp_path):
    checks, code = run_doctor(str(tmp_path / "nope"), probe=UP)
    assert code == 1
    assert checks[0]["status"] == FAIL


def test_non_entity_directory_is_fail(tmp_path):
    (tmp_path / "justdir").mkdir()
    checks, code = run_doctor(str(tmp_path / "justdir"), probe=UP)
    assert code == 1
    assert "not an entity root" in checks[0]["reason"]


def test_corrupt_drives_json_is_fail(root):
    (root / "identity" / "drives.json").write_text("{not json")
    checks, code = run_doctor(str(root), probe=UP)
    assert code == 1
    assert _by_name(checks)["identity/drives.json"]["status"] == FAIL


def test_corrupt_sense_config_is_fail(root):
    (root / "senses" / "web.json").write_text("]]]")
    checks, code = run_doctor(str(root), probe=UP)
    assert code == 1
    assert _by_name(checks)["senses/web.json"]["status"] == FAIL


def test_http_config_without_token_is_fail(root):
    (root / "senses" / "http.json").write_text('{"port": 8760}')
    checks, code = run_doctor(str(root), probe=UP)
    assert code == 1
    assert "token" in _by_name(checks)["senses/http.json"]["reason"]


def test_corrupt_sqlite_store_is_fail(root):
    (root / "memory" / "memory.sqlite").write_bytes(b"garbage" * 100)
    checks, code = run_doctor(str(root), probe=UP)
    assert code == 1
    store = _by_name(checks)["store memory/memory.sqlite"]
    assert store["status"] == FAIL


# ── CLI wiring ────────────────────────────────────────────────────────

def test_doctor_subcommand_human_output(root, capsys, monkeypatch):
    import anima.doctor as doctor
    monkeypatch.setattr(doctor, "_http_reachable", DOWN)
    assert main(["doctor", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "verdict: fit to wake (with warnings)" in out
    assert "PASS" in out and "WARN" in out


def test_doctor_subcommand_json_output(root, capsys, monkeypatch):
    import anima.doctor as doctor
    monkeypatch.setattr(doctor, "_http_reachable", DOWN)
    (root / "identity" / "routing.json").write_text("broken")
    assert main(["doctor", "--root", str(root), "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["exit"] == 1
    assert doc["counts"]["FAIL"] >= 1
    assert any(c["status"] == "FAIL" for c in doc["checks"])
