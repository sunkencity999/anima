"""anima backup — snapshots of a life, taken while it's being lived.

An entity root IS the being (ARCHITECTURE.md); losing the directory is
losing the entity. This module makes timestamped tar.gz snapshots that
are safe to take while the runtime is live:

- sqlite stores (*.sqlite, *.db) are captured through the sqlite3
  backup API — a transactionally consistent copy even mid-write under
  WAL — never a raw byte copy. Their -wal/-shm sidecars are therefore
  excluded from the archive (the API snapshot already folds them in).
- runtime scratch never travels: runtime.log, runtime.pid, sockets,
  temp files, __pycache__. A backup is a body at rest, not a running
  process.
- archives are written to a temp name and renamed into place, so a
  crash mid-write can't leave a plausible-looking corpse that later
  gets restored.
- pruning keeps the newest N (default 14): a shelf of recent selves,
  not an unbounded museum.

Default destination is <root>/../anima-backups/<rootname>/ — outside
the root itself, so backups never back up backups.

Pure stdlib, like everything here.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import time
from typing import List, Optional, Tuple

# basenames that never belong in a snapshot
EXCLUDE_BASENAMES = ("runtime.log", "runtime.pid")
# suffixes for runtime scratch / sqlite sidecars (superseded by the
# backup-API snapshot of their parent database)
EXCLUDE_SUFFIXES = (".sock", ".tmp", "-wal", "-shm", "-journal")
SQLITE_SUFFIXES = (".sqlite", ".db")


def _excluded(name: str) -> bool:
    base = os.path.basename(name)
    if base in EXCLUDE_BASENAMES or base == "__pycache__":
        return True
    return base.endswith(EXCLUDE_SUFFIXES)


def _snapshot_sqlite(src_path: str, snap_path: str) -> None:
    """Consistent point-in-time copy via the sqlite3 backup API."""
    os.makedirs(os.path.dirname(snap_path), exist_ok=True)
    src = sqlite3.connect(src_path, timeout=30)
    try:
        dst = sqlite3.connect(snap_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def default_dest(root: str) -> str:
    root = os.path.abspath(root)
    name = os.path.basename(root.rstrip(os.sep)) or "entity"
    return os.path.join(os.path.dirname(root), "anima-backups", name)


def create_backup(root: str, dest: Optional[str] = None, *,
                  keep: int = 14,
                  now: Optional[float] = None) -> Tuple[str, List[str]]:
    """Snapshot `root` into dest; prune to the newest `keep` archives.

    Returns (archive_path, pruned_paths). Safe against a live runtime.
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"no directory at {root}")
    rootname = os.path.basename(root.rstrip(os.sep)) or "entity"
    dest = os.path.abspath(dest) if dest else default_dest(root)
    if dest == root or dest.startswith(root + os.sep):
        raise ValueError("backup destination must be outside the root "
                         "(backups never back up backups)")
    os.makedirs(dest, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S",
                          time.localtime(now if now is not None
                                         else time.time()))
    out_path = os.path.join(dest, f"{rootname}-{stamp}.tar.gz")

    with tempfile.TemporaryDirectory(prefix="anima-backup-") as tmp:
        snap_dir = os.path.join(tmp, "sqlite")
        tmp_tar = os.path.join(tmp, "archive.tar.gz")
        with tarfile.open(tmp_tar, "w:gz") as tar:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in sorted(dirnames)
                               if d != "__pycache__"]
                for fn in sorted(filenames):
                    full = os.path.join(dirpath, fn)
                    if _excluded(full) or not os.path.isfile(full):
                        continue
                    rel = os.path.relpath(full, root)
                    if fn.endswith(SQLITE_SUFFIXES):
                        snap = os.path.join(snap_dir, rel)
                        _snapshot_sqlite(full, snap)
                        tar.add(snap, arcname=rel)
                    else:
                        tar.add(full, arcname=rel)
        # rename into place last: a partial archive never wears the
        # name of a real one
        os.replace(tmp_tar, out_path)

    pruned = _prune(dest, rootname, keep)
    return out_path, pruned


def _prune(dest: str, rootname: str, keep: int) -> List[str]:
    if keep < 1:
        keep = 1
    prefix = rootname + "-"
    archives = sorted(
        f for f in os.listdir(dest)
        if f.startswith(prefix) and f.endswith(".tar.gz"))
    pruned = []
    for name in archives[:-keep] if len(archives) > keep else []:
        path = os.path.join(dest, name)
        os.remove(path)
        pruned.append(path)
    return pruned


def cmd_backup(args) -> int:
    """CLI entry (wired in anima.cli). One machine-readable line out."""
    root = os.path.abspath(args.root)
    if not os.path.exists(os.path.join(root, "identity", "lineage.log")):
        print(f"no entity root at {root} (identity/lineage.log missing); "
              f"nothing to back up", file=sys.stderr)
        return 1
    try:
        path, pruned = create_backup(root, args.dest, keep=args.keep)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"backup": path,
                      "bytes": os.path.getsize(path),
                      "pruned": len(pruned)}))
    return 0
