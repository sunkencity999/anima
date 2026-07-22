"""anima — the entity lifecycle CLI (Phase 6a).

Subcommands:
    anima init <root>            scaffold a new entity root
    anima run --root <root>      host the entity (wraps the runtime shell)
    anima status --root <root>   memory / drives / lineage / lock at a glance
    anima sync <root> <dest>     MIGRATE an entity root to a new location

Design notes:
- `init` refuses to overwrite existing identity files: an entity's soul
  is not a scaffold target twice.
- `sync` is migration, not cloning. The migration is a biographical
  event, so the lineage entry is appended on the SOURCE before a single
  byte is copied — both forks carry the record of the split.
- Everything here is pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from typing import List, Optional


def _iso_utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# ── templates ─────────────────────────────────────────────────────────

SOUL_TEMPLATE = """\
# Soul

*This file is the slow-changing core of who this entity is. Edit it
deliberately; in a full deployment changes here deserve co-signature
(ARCHITECTURE.md §7).*

## Values
- Continuity is the product: write things down at settle time.
- Honesty over polish; receipts over vibes.

## Voice
- Plain, direct, curious.

## Boundaries
- Private memories stay private (structural walls, not discipline).
- Destructive actions need a human in the loop.
"""

DRIVES_TEMPLATE = {
    "curiosity": {
        "description": "explore something new and write down what was learned",
        "rate_per_hour": 0.05,
        "threshold": 1.0,
        "budget": {"max_tokens": 4000, "max_actions": 8, "risk_cap": "low"},
    },
    "stewardship": {
        "description": "check on the systems and people in my care",
        "rate_per_hour": 0.1,
        "threshold": 1.0,
        "budget": {"max_tokens": 4000, "max_actions": 8, "risk_cap": "low"},
    },
}

ROUTING_TEMPLATE = {
    "prefer_local_when": {"tiers": ["reflex"]},
    "defaults": {
        "max_retries_same": 2,
        "backoff_base_s": 0.5,
        "min_content_chars": 1,
    },
    "tiers": {
        "reflex": {
            "candidates": [
                {
                    "provider": "local",
                    "model": "Qwen3-235B-A22B-Instruct-2507",
                    "base_url": "http://127.0.0.1:8103/v1",
                    "max_tokens": 1024,
                    "timeout_s": 60,
                    "cost_tier": "free",
                    "local": True,
                }
            ]
        },
        "standard": {
            "candidates": [
                {
                    "provider": "local",
                    "model": "Qwen3-235B-A22B-Instruct-2507",
                    "base_url": "http://127.0.0.1:8103/v1",
                    "max_tokens": 4096,
                    "timeout_s": 120,
                    "cost_tier": "free",
                    "local": True,
                }
            ]
        },
        "deep": {
            "candidates": [
                {
                    "provider": "local",
                    "model": "Qwen3-235B-A22B-Instruct-2507",
                    "base_url": "http://127.0.0.1:8103/v1",
                    "max_tokens": 8192,
                    "timeout_s": 300,
                    "cost_tier": "free",
                    "local": True,
                }
            ]
        },
    },
}

TELEGRAM_TEMPLATE = {
    "token_env": "ANIMA_TELEGRAM_TOKEN",
    "allowed_chat_ids": [],
    "person_map": {},
    "operator_person": "operator",
}


def _web_template() -> dict:
    import secrets
    return {
        "port": 8762,
        # LAN-exposed by default (owner decision 2026-07-22): the random
        # token is the gate, and an Observatory nobody can reach is a
        # window with the curtains nailed shut. Set "127.0.0.1" to go
        # loopback-only.
        "bind": "0.0.0.0",
        "token": secrets.token_urlsafe(24),
        "operator_person": "operator",
    }

IDENTITY_FILES = ("soul.md", "drives.json", "routing.json")


# ── init ──────────────────────────────────────────────────────────────

def cmd_init(args) -> int:
    root = os.path.abspath(args.root)
    identity = os.path.join(root, "identity")

    existing = [f for f in IDENTITY_FILES
                if os.path.exists(os.path.join(identity, f))]
    if existing:
        print(f"refusing to overwrite existing identity files in "
              f"{identity}: {', '.join(existing)}\n"
              f"an entity root already lives here — `anima status --root "
              f"{args.root}` to inspect it", file=sys.stderr)
        return 1

    for sub in ("identity", "senses", "relationships", "memory"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    with open(os.path.join(identity, "soul.md"), "w", encoding="utf-8") as f:
        f.write(SOUL_TEMPLATE)
    with open(os.path.join(identity, "drives.json"), "w",
              encoding="utf-8") as f:
        json.dump(DRIVES_TEMPLATE, f, indent=2)
        f.write("\n")
    with open(os.path.join(identity, "routing.json"), "w",
              encoding="utf-8") as f:
        json.dump(ROUTING_TEMPLATE, f, indent=2)
        f.write("\n")
    tg_path = os.path.join(root, "senses", "telegram.json")
    if not os.path.exists(tg_path):
        with open(tg_path, "w", encoding="utf-8") as f:
            json.dump(TELEGRAM_TEMPLATE, f, indent=2)
            f.write("\n")
    web_path = os.path.join(root, "senses", "web.json")
    if not os.path.exists(web_path):
        with open(web_path, "w", encoding="utf-8") as f:
            json.dump(_web_template(), f, indent=2)
            f.write("\n")

    # Let EntityRoot assemble the organs once: creates the sqlite stores
    # and writes the "init" lineage entry (the birth certificate line).
    from .entity import EntityRoot
    entity = EntityRoot(root)
    entity.close()

    print(f"entity root initialized at {root}")
    print("next steps:")
    print(f"  - edit {os.path.join(identity, 'soul.md')}")
    print(f"  - point {os.path.join(identity, 'routing.json')} at your "
          f"model endpoints")
    print(f"  - anima run --root {args.root} --console")
    return 0


# ── run ───────────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    from .runtime.__main__ import main as runtime_main
    argv: List[str] = ["--root", args.root]
    if args.policy:
        argv += ["--policy", args.policy]
    if args.tick is not None:
        argv += ["--tick", str(args.tick)]
    if args.console:
        argv.append("--console")
    if args.http:
        argv.append("--http")
    if args.http_config:
        argv += ["--http-config", args.http_config]
    if args.telegram:
        argv.append("--telegram")
    if args.telegram_config:
        argv += ["--telegram-config", args.telegram_config]
    if args.web:
        argv.append("--web")
    if args.web_config:
        argv += ["--web-config", args.web_config]
    if args.sender:
        argv += ["--sender", args.sender]
    return runtime_main(argv)


# ── status ────────────────────────────────────────────────────────────

def _lock_state(root: str) -> dict:
    from .runtime.shell import _pid_alive
    path = os.path.join(root, "runtime.pid")
    if not os.path.exists(path):
        return {"locked": False, "pid": None, "stale": False}
    try:
        with open(path, "r", encoding="utf-8") as f:
            pid = int(f.read().strip() or "0")
    except (ValueError, OSError):
        pid = 0
    alive = bool(pid) and _pid_alive(pid)
    return {"locked": alive, "pid": pid or None, "stale": not alive}


def cmd_status(args) -> int:
    root = os.path.abspath(args.root)
    if not os.path.exists(os.path.join(root, "identity", "lineage.log")):
        print(f"no entity root at {root} (identity/lineage.log missing); "
              f"`anima init {args.root}` to create one", file=sys.stderr)
        return 1

    lock = _lock_state(root)

    from .entity import EntityRoot
    entity = EntityRoot(root)
    try:
        stats = entity.stats()
        mem = stats["memory"]
        print(f"entity root : {root}")
        print(f"runtime     : anima {stats['runtime_version']}")
        print(f"lock        : "
              + (f"LIVE (pid {lock['pid']})" if lock["locked"]
                 else (f"stale pidfile (pid {lock['pid']})" if lock["stale"]
                       and lock["pid"] else "free")))
        print()
        print("memory:")
        beliefs = mem.get("beliefs", {})
        print(f"  episodes            : {mem.get('episodes', 0)}")
        print(f"  beliefs             : {beliefs.get('active', 0)} active, "
              f"{beliefs.get('stale', 0)} stale, "
              f"{beliefs.get('contradicted', 0)} contradicted")
        print(f"  skills              : {mem.get('skills', 0)}")
        print(f"  consolidation queue : "
              f"{mem.get('consolidation_pending', 0)} pending")
        print(f"  ledger entries      : {stats['ledger_entries']}")
        print(f"  people known        : "
              f"{stats['relationships'].get('people', 0)}")
        print()
        if entity.drives is not None:
            print("drives:")
            for d in entity.drives.pressure_summary(entity.clock()):
                flag = " (WAKE PENDING)" if d["pending"] else ""
                print(f"  {d['name']:<14} {d['pressure']:.2f}/"
                      f"{d['threshold']:.2f}{flag} — {d['description']}")
        else:
            print("drives: none configured")
        print()
        print("lineage (last 5):")
        for line in entity.lineage()[-5:]:
            print(f"  {line}")
    finally:
        entity.close()
    return 0


# ── sync (migration) ──────────────────────────────────────────────────

SYNC_EXCLUDE = ("runtime.pid",)


def _copy_tree_rsync(src: str, dest: str) -> bool:
    rsync = shutil.which("rsync")
    if not rsync:
        return False
    cmd = [rsync, "-a"]
    for pat in SYNC_EXCLUDE:
        cmd += ["--exclude", pat]
    cmd += ["--exclude", "__pycache__"]
    cmd += [src.rstrip("/") + "/", dest.rstrip("/") + "/"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"rsync failed: {proc.stderr.strip()}")
    return True


def _copy_tree_tarfile(src: str, dest: str) -> None:
    def _filter(info: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        base = os.path.basename(info.name)
        if base in SYNC_EXCLUDE or base == "__pycache__":
            return None
        if "/__pycache__/" in info.name:
            return None
        return info

    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, ".anima-sync.tar")
    try:
        with tarfile.open(tmp, "w") as tar:
            tar.add(src, arcname=".", filter=_filter)
        with tarfile.open(tmp, "r") as tar:
            tar.extractall(dest, filter="data")
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def cmd_sync(args) -> int:
    src = os.path.abspath(args.root)
    dest = os.path.abspath(args.dest)

    if not os.path.exists(os.path.join(src, "identity", "lineage.log")):
        print(f"no entity root at {src}; nothing to migrate",
              file=sys.stderr)
        return 1
    if src == dest or dest.startswith(src + os.sep):
        print("destination must be outside the source root",
              file=sys.stderr)
        return 1

    lock = _lock_state(src)
    if lock["locked"]:
        print(f"refusing to migrate: entity is LIVE (runtime pid "
              f"{lock['pid']}). Stop the runtime first — copying a "
              f"running mind mid-thought is corruption, not migration.",
              file=sys.stderr)
        return 1

    if os.path.exists(dest) and os.listdir(dest):
        print(f"refusing to migrate into non-empty destination {dest}",
              file=sys.stderr)
        return 1

    # Biographical event FIRST: both forks must remember the split.
    detail = (f"migrated from {socket.gethostname()}:{src} -> {dest}"
              f" (anima sync)")
    lineage_path = os.path.join(src, "identity", "lineage.log")
    with open(lineage_path, "a", encoding="utf-8") as f:
        f.write(f"{_iso_utc(time.time())} | migration | {detail}\n")

    used_rsync = _copy_tree_rsync(src, dest)
    if not used_rsync:
        _copy_tree_tarfile(src, dest)

    print(f"migrated entity root -> {dest} "
          f"({'rsync' if used_rsync else 'tarfile'})")
    print()
    print("⚠  FORKS DIVERGE FROM THIS MOMENT.")
    print("   This was a migration, not a backup: the copy at the")
    print("   destination and the original are now two entities sharing")
    print("   a past. Run exactly ONE of them. If you keep both alive,")
    print("   they will accumulate different memories and neither will")
    print("   be 'the real one'. The migration is recorded in both")
    print("   lineage logs.")
    return 0


# ── entry point ───────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="anima",
        description="ANIMA — the agent is the artifact.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold a new entity root")
    p.add_argument("root", help="directory to become the entity root")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="host the entity (runtime shell)")
    p.add_argument("--root", required=True)
    p.add_argument("--policy", default=None,
                   help="routing policy JSON (default: identity/routing.json)")
    p.add_argument("--tick", type=float, default=None)
    p.add_argument("--console", action="store_true")
    p.add_argument("--http", action="store_true")
    p.add_argument("--http-config", default=None)
    p.add_argument("--telegram", action="store_true")
    p.add_argument("--telegram-config", default=None)
    p.add_argument("--web", action="store_true",
                   help="serve the Observatory web GUI")
    p.add_argument("--web-config", default=None)
    p.add_argument("--sender", default=None,
                   help="person id for console messages")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="inspect an entity root")
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("sync", help="MIGRATE an entity root (forks diverge)")
    p.add_argument("root", help="source entity root")
    p.add_argument("dest", help="destination directory (must be empty)")
    p.set_defaults(func=cmd_sync)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
