"""anima backup — snapshot creation, exclusions, pruning, sqlite safety.

All offline, all in tmp roots. The sqlite-consistency test writes
through a live WAL connection and verifies the snapshot carries the
committed row — the exact scenario of backing up a running entity.
"""

import json
import os
import sqlite3
import tarfile

import pytest

from anima.backup import create_backup, default_dest
from anima.cli import main


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "ent"
    assert main(["init", str(root)]) == 0
    # runtime scratch that must never travel
    (root / "runtime.log").write_text("noise\n")
    (root / "runtime.pid").write_text("12345\n")
    (root / "senses" / "stale.sock").write_text("")
    return root


def _names(archive):
    with tarfile.open(archive, "r:gz") as tar:
        return set(tar.getnames())


# ── create ────────────────────────────────────────────────────────────

def test_backup_creates_archive_with_entity_content(root, tmp_path):
    dest = tmp_path / "backups"
    path, pruned = create_backup(str(root), str(dest))
    assert os.path.exists(path)
    assert os.path.basename(path).startswith("ent-")
    assert pruned == []

    names = _names(path)
    assert "identity/soul.md" in names
    assert "identity/lineage.log" in names
    assert "senses/http.json" in names
    assert "memory/memory.sqlite" in names


def test_backup_excludes_runtime_scratch_and_wal(root, tmp_path):
    names = _names(create_backup(str(root), str(tmp_path / "b"))[0])
    assert "runtime.log" not in names
    assert "runtime.pid" not in names
    assert "senses/stale.sock" not in names
    # sqlite sidecars are folded into the backup-API snapshot
    assert not any(n.endswith(("-wal", "-shm")) for n in names)


def test_backup_default_dest_is_sibling_of_root(root):
    path, _ = create_backup(str(root))
    assert os.path.dirname(path) == default_dest(str(root))
    assert os.path.dirname(path) == os.path.join(
        os.path.dirname(str(root)), "anima-backups", "ent")


def test_backup_refuses_dest_inside_root(root):
    with pytest.raises(ValueError):
        create_backup(str(root), str(root / "backups"))


# ── prune ─────────────────────────────────────────────────────────────

def test_backup_prunes_to_keep(root, tmp_path):
    dest = tmp_path / "b"
    # distinct timestamps via injected clocks — no sleeping in tests
    for i in range(5):
        path, pruned = create_backup(str(root), str(dest), keep=3,
                                     now=1000000000 + i)
    archives = sorted(os.listdir(dest))
    assert len(archives) == 3
    # the newest survives, the oldest two are gone
    assert os.path.basename(path) in archives
    assert len(pruned) == 1  # fifth run pruned exactly one


# ── sqlite consistency ────────────────────────────────────────────────

def test_backup_snapshots_sqlite_consistently_under_wal(root, tmp_path):
    db_path = root / "memory" / "memory.sqlite"
    live = sqlite3.connect(db_path)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE IF NOT EXISTS canary (v TEXT)")
    live.execute("INSERT INTO canary VALUES ('alive-at-backup-time')")
    live.commit()
    # live connection stays OPEN across the backup — the running-entity
    # scenario
    path, _ = create_backup(str(root), str(tmp_path / "b"))
    live.close()

    extract = tmp_path / "restore"
    with tarfile.open(path, "r:gz") as tar:
        tar.extractall(extract, filter="data")
    snap = sqlite3.connect(extract / "memory" / "memory.sqlite")
    try:
        rows = snap.execute("SELECT v FROM canary").fetchall()
        assert rows == [("alive-at-backup-time",)]
        assert snap.execute(
            "PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        snap.close()


# ── CLI wiring ────────────────────────────────────────────────────────

def test_backup_subcommand_prints_machine_readable_line(root, tmp_path,
                                                        capsys):
    dest = tmp_path / "b"
    assert main(["backup", "--root", str(root),
                 "--dest", str(dest), "--keep", "2"]) == 0
    doc = json.loads(capsys.readouterr().out.strip())
    assert os.path.exists(doc["backup"])
    assert doc["bytes"] > 0
    assert doc["pruned"] == 0


def test_backup_subcommand_refuses_non_root(tmp_path, capsys):
    assert main(["backup", "--root", str(tmp_path / "nope")]) == 1
    assert "no entity root" in capsys.readouterr().err
