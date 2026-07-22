"""Phase 6a — packaging + CLI: init scaffold, status, sync-as-migration.

All offline; every command runs in-process via anima.cli.main().
"""

import json
import os

import pytest

from anima.cli import main


def _init(tmp_path, name="ent"):
    root = tmp_path / name
    assert main(["init", str(root)]) == 0
    return root


# ── init ──────────────────────────────────────────────────────────────

def test_init_scaffolds_entity_root(tmp_path):
    root = _init(tmp_path)
    identity = root / "identity"
    assert (identity / "soul.md").exists()
    assert (identity / "drives.json").exists()
    assert (identity / "routing.json").exists()
    assert (identity / "lineage.log").exists()
    assert (root / "senses" / "telegram.json").exists()
    for sub in ("senses", "relationships", "memory"):
        assert (root / sub).is_dir()

    drives = json.loads((identity / "drives.json").read_text())
    assert "curiosity" in drives and "stewardship" in drives
    routing = json.loads((identity / "routing.json").read_text())
    assert "127.0.0.1:8103" in json.dumps(routing)

    lineage = (identity / "lineage.log").read_text()
    assert "init" in lineage


def test_init_refuses_overwrite(tmp_path, capsys):
    root = _init(tmp_path)
    soul = root / "identity" / "soul.md"
    soul.write_text("# My hand-edited soul\n")
    assert main(["init", str(root)]) == 1
    # nothing was clobbered
    assert soul.read_text() == "# My hand-edited soul\n"
    assert "refusing" in capsys.readouterr().err.lower()


def test_init_scaffolds_open_web_config_by_default(tmp_path):
    root = _init(tmp_path)
    web = json.loads((root / "senses" / "web.json").read_text())
    assert web["auth"] == "open"
    assert "token" not in web
    assert web["bind"] == "0.0.0.0"


def test_init_auth_token_scaffolds_gated_web_config(tmp_path):
    root = tmp_path / "gated"
    assert main(["init", str(root), "--auth", "token"]) == 0
    web = json.loads((root / "senses" / "web.json").read_text())
    assert web["auth"] == "token"
    assert len(web["token"]) >= 24


# ── status ────────────────────────────────────────────────────────────

def test_status_reports_memory_drives_lineage_lock(tmp_path, capsys):
    root = _init(tmp_path)
    assert main(["status", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "memory:" in out
    assert "episodes" in out
    assert "curiosity" in out and "stewardship" in out  # drive pressures
    assert "lineage (last 5):" in out
    assert "init" in out
    assert "lock        : free" in out


def test_status_shows_live_lock(tmp_path, capsys):
    root = _init(tmp_path)
    (root / "runtime.pid").write_text(f"{os.getpid()}\n")
    assert main(["status", "--root", str(root)]) == 0
    assert f"LIVE (pid {os.getpid()})" in capsys.readouterr().out


def test_status_on_missing_root_fails(tmp_path, capsys):
    assert main(["status", "--root", str(tmp_path / "nope")]) == 1
    assert "no entity root" in capsys.readouterr().err


# ── sync (migration) ──────────────────────────────────────────────────

def test_sync_refuses_when_runtime_live(tmp_path, capsys):
    root = _init(tmp_path)
    (root / "runtime.pid").write_text(f"{os.getpid()}\n")  # us: alive
    dest = tmp_path / "dest"
    assert main(["sync", str(root), str(dest)]) == 1
    assert "LIVE" in capsys.readouterr().err
    assert not dest.exists()
    # and no migration entry was written on refusal
    assert "migration" not in (root / "identity" / "lineage.log").read_text()


def test_sync_migrates_and_records_lineage_on_both_forks(tmp_path, capsys):
    root = _init(tmp_path)
    # stale pidfile (dead pid) must NOT block migration
    (root / "runtime.pid").write_text("999999999\n")
    dest = tmp_path / "dest"
    assert main(["sync", str(root), str(dest)]) == 0
    out = capsys.readouterr().out
    assert "FORKS DIVERGE" in out

    src_lineage = (root / "identity" / "lineage.log").read_text()
    dst_lineage = (dest / "identity" / "lineage.log").read_text()
    assert "| migration |" in src_lineage
    # entry appended BEFORE copying → the copy carries it too
    assert "| migration |" in dst_lineage
    # entity content travelled
    assert (dest / "identity" / "soul.md").exists()
    assert (dest / "memory").is_dir()
    # the pidfile does not travel: a copy is not a running process
    assert not (dest / "runtime.pid").exists()


def test_sync_refuses_nonempty_dest(tmp_path, capsys):
    root = _init(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "junk.txt").write_text("x")
    assert main(["sync", str(root), str(dest)]) == 1
    assert "non-empty" in capsys.readouterr().err


def test_sync_tarfile_fallback_when_no_rsync(tmp_path, monkeypatch):
    import anima.cli as cli
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    root = _init(tmp_path)
    dest = tmp_path / "dest"
    assert main(["sync", str(root), str(dest)]) == 0
    assert (dest / "identity" / "soul.md").exists()
    assert "| migration |" in (dest / "identity" / "lineage.log").read_text()
    assert not (dest / "runtime.pid").exists()


# ── run wiring (no loop started) ──────────────────────────────────────

def test_run_subcommand_forwards_to_runtime_main(tmp_path, monkeypatch):
    captured = {}

    def fake_runtime_main(argv):
        captured["argv"] = argv
        return 0

    import anima.runtime.__main__ as rt
    monkeypatch.setattr(rt, "main", fake_runtime_main)
    root = _init(tmp_path)
    assert main(["run", "--root", str(root), "--telegram", "--http"]) == 0
    assert "--telegram" in captured["argv"]
    assert "--http" in captured["argv"]
    assert "--root" in captured["argv"]
