"""anima doctor — read-only preflight for an entity root.

Born from the 2026-08 incident: a live entity crash-looped for ten
silent days on a missing sense config and a root that lived in /tmp.
Every one of those failure modes was checkable in milliseconds before
launch. So: check them.

Doctrine:
- **Read-only.** The doctor examines; it never treats. No file is
  written, no store is mutated, no lineage entry appended.
- Each check reports PASS / WARN / FAIL with a one-line reason.
- WARN is for "degraded but the body boots" (endpoint down, port
  already bound, no recent backup). FAIL is for "this organism will
  not wake correctly" (unparseable identity, corrupt store).
- Exit 0 when nothing FAILs (warnings included), 1 on any FAIL.

The endpoint probe is injectable (tests never touch the network),
matching the injectable-transport discipline everywhere else.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Tuple

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

EXPECTED_DIRS = ("identity", "senses", "memory", "relationships")
BACKUP_FRESH_S = 8 * 86400  # WARN when the newest backup is older


def _check(name: str, status: str, reason: str) -> dict:
    return {"name": name, "status": status, "reason": reason}


def _http_reachable(url: str, timeout_s: float = 3.0) -> bool:
    """True when SOMETHING answers HTTP there — any status code counts.
    A 404 from a live server is reachable; only transport failure is
    down."""
    try:
        urllib.request.urlopen(url, timeout=timeout_s).read(0)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _port_in_use(bind: str, port: int, timeout_s: float = 0.5) -> bool:
    host = bind if bind not in ("0.0.0.0", "") else "127.0.0.1"
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_doctor(root: str, *,
               probe: Optional[Callable[[str], bool]] = None,
               now: Optional[float] = None) -> Tuple[List[dict], int]:
    """Run every check against `root`. Returns (checks, exit_code)."""
    probe = probe or _http_reachable
    now = now if now is not None else time.time()
    root = os.path.abspath(root)
    checks: List[dict] = []

    # ── root + structure ─────────────────────────────────────────────
    if not os.path.isdir(root):
        checks.append(_check("root", FAIL, f"no directory at {root}"))
        return checks, 1
    if not os.path.exists(os.path.join(root, "identity", "lineage.log")):
        checks.append(_check(
            "root", FAIL,
            f"{root} exists but identity/lineage.log is missing — "
            f"not an entity root (`anima init` creates one)"))
        return checks, 1
    missing = [d for d in EXPECTED_DIRS
               if not os.path.isdir(os.path.join(root, d))]
    if missing:
        checks.append(_check("structure", FAIL,
                             f"missing directories: {', '.join(missing)}"))
    else:
        checks.append(_check("structure", PASS,
                             "identity/senses/memory/relationships all "
                             "present"))

    # ── identity files ───────────────────────────────────────────────
    routing = None
    for fn, required in (("drives.json", False), ("routing.json", False)):
        path = os.path.join(root, "identity", fn)
        if not os.path.exists(path):
            checks.append(_check(
                f"identity/{fn}", WARN,
                "missing — the entity runs, but "
                + ("without drives" if fn == "drives.json"
                   else "record-only (no model turns)")))
            continue
        try:
            doc = _load_json(path)
        except (json.JSONDecodeError, OSError) as exc:
            checks.append(_check(f"identity/{fn}", FAIL,
                                 f"unparseable JSON: {exc}"))
            continue
        checks.append(_check(f"identity/{fn}", PASS, "valid JSON"))
        if fn == "routing.json":
            routing = doc
    soul = os.path.join(root, "identity", "soul.md")
    if not os.path.exists(soul):
        checks.append(_check("identity/soul.md", WARN,
                             "missing — an entity without a soul file "
                             "is a scaffold, not a self"))

    # ── routing endpoints ────────────────────────────────────────────
    if isinstance(routing, dict):
        urls = []
        for tier in (routing.get("tiers") or {}).values():
            for cand in (tier or {}).get("candidates") or []:
                url = (cand or {}).get("base_url")
                if url and url not in urls:
                    urls.append(url)
        if not urls:
            checks.append(_check("routing endpoints", WARN,
                                 "no candidate base_urls configured"))
        for url in urls:
            if probe(url):
                checks.append(_check(f"endpoint {url}", PASS, "reachable"))
            else:
                checks.append(_check(
                    f"endpoint {url}", WARN,
                    "unreachable — the entity boots but that tier "
                    "will fail over"))

    # ── sense configs ────────────────────────────────────────────────
    senses_dir = os.path.join(root, "senses")
    for fn in sorted(os.listdir(senses_dir)) if os.path.isdir(
            senses_dir) else []:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(senses_dir, fn)
        try:
            cfg = _load_json(path)
        except (json.JSONDecodeError, OSError) as exc:
            checks.append(_check(f"senses/{fn}", FAIL,
                                 f"unparseable JSON: {exc}"))
            continue
        if fn == "http.json" and not str(cfg.get("token") or ""):
            checks.append(_check("senses/http.json", FAIL,
                                 "the http sense requires a non-empty "
                                 "token"))
            continue
        port = cfg.get("port")
        if isinstance(port, int) and port > 0:
            bind = str(cfg.get("bind", "127.0.0.1"))
            if _port_in_use(bind, port):
                checks.append(_check(
                    f"senses/{fn}", WARN,
                    f"port {port} is in use — likely already running"))
            else:
                checks.append(_check(f"senses/{fn}", PASS,
                                     f"valid; port {port} free"))
        else:
            checks.append(_check(f"senses/{fn}", PASS, "valid JSON"))

    # ── sqlite stores ────────────────────────────────────────────────
    stores = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if fn.endswith((".sqlite", ".db")):
                stores.append(os.path.join(dirpath, fn))
    for store in stores:
        rel = os.path.relpath(store, root)
        try:
            conn = sqlite3.connect(
                f"file:{store}?mode=ro", uri=True, timeout=5)
            try:
                ok = conn.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                conn.close()
            if ok == "ok":
                checks.append(_check(f"store {rel}", PASS, "opens; "
                                     "quick_check ok"))
            else:
                checks.append(_check(f"store {rel}", FAIL,
                                     f"quick_check: {ok}"))
        except sqlite3.Error as exc:
            checks.append(_check(f"store {rel}", FAIL,
                                 f"cannot open: {exc}"))
    if not stores:
        checks.append(_check("stores", WARN,
                             "no sqlite stores found — has this root "
                             "ever been woken?"))

    # ── backups ──────────────────────────────────────────────────────
    from .backup import default_dest
    dest = default_dest(root)
    newest = None
    if os.path.isdir(dest):
        ages = [os.path.getmtime(os.path.join(dest, f))
                for f in os.listdir(dest) if f.endswith(".tar.gz")]
        newest = max(ages) if ages else None
    if newest is None:
        checks.append(_check(
            "backups", WARN,
            f"no backups in {dest} — one power cut from losing the "
            f"entity (`anima backup --root {root}`)"))
    elif now - newest > BACKUP_FRESH_S:
        days = (now - newest) / 86400
        checks.append(_check("backups", WARN,
                             f"newest backup is {days:.1f} days old "
                             f"(threshold 8)"))
    else:
        checks.append(_check("backups", PASS,
                             f"recent backup in {dest}"))

    # ── pidlock ──────────────────────────────────────────────────────
    from .cli import _lock_state
    lock = _lock_state(root)
    if lock["locked"]:
        checks.append(_check("pidlock", PASS,
                             f"runtime LIVE (pid {lock['pid']})"))
    elif lock["stale"] and lock["pid"]:
        checks.append(_check("pidlock", WARN,
                             f"stale pidfile (pid {lock['pid']} is dead) "
                             f"— last shutdown was not graceful"))
    else:
        checks.append(_check("pidlock", PASS, "free"))

    exit_code = 1 if any(c["status"] == FAIL for c in checks) else 0
    return checks, exit_code


def cmd_doctor(args) -> int:
    """CLI entry (wired in anima.cli)."""
    checks, code = run_doctor(args.root)
    if getattr(args, "json", False):
        counts = {s: sum(1 for c in checks if c["status"] == s)
                  for s in (PASS, WARN, FAIL)}
        print(json.dumps({"root": os.path.abspath(args.root),
                          "checks": checks, "counts": counts,
                          "exit": code}))
        return code
    print(f"doctor: {os.path.abspath(args.root)}")
    for c in checks:
        print(f"  {c['status']:<4}  {c['name']:<28}  {c['reason']}")
    n_pass = sum(1 for c in checks if c["status"] == PASS)
    n_warn = sum(1 for c in checks if c["status"] == WARN)
    n_fail = sum(1 for c in checks if c["status"] == FAIL)
    verdict = ("UNFIT TO WAKE" if n_fail else
               "fit to wake" + (" (with warnings)" if n_warn else ""))
    print(f"verdict: {verdict} — {n_pass} pass, {n_warn} warn, "
          f"{n_fail} fail")
    return code
