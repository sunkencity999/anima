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
import urllib.request
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

# Drive budgets scaffold at risk_cap "normal" so drives can *reach*
# (Phase 8a: the notify tool is risk "medium") — a drive that notices
# something worth the person's pocket may say so. The per-settle notify
# rail caps spam structurally; drop a drive back to "low" to make it
# contemplative-only.
DRIVES_TEMPLATE = {
    "curiosity": {
        "description": "explore something new and write down what was learned",
        "rate_per_hour": 0.05,
        "threshold": 1.0,
        "budget": {"max_tokens": 4000, "max_actions": 8,
                   "risk_cap": "normal"},
    },
    "stewardship": {
        "description": "check on the systems and people in my care",
        "rate_per_hour": 0.1,
        "threshold": 1.0,
        "budget": {"max_tokens": 4000, "max_actions": 8,
                   "risk_cap": "normal"},
    },
}

# Default local endpoint the scaffold points at. The MODEL is never
# baked in (Phase 7 §6, owner directive 2026-08-05: the Observatory
# once reported a retired model because a template lied): init asks
# the endpoint what it serves and writes "unknown" when nobody answers.
DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:8103/v1"


def probe_endpoint_model(base_url: str = DEFAULT_LOCAL_ENDPOINT,
                         timeout_s: float = 3.0) -> str:
    """GET <base_url>/models and return the first model id the endpoint
    reports, or "unknown". Introspection, not hardcoding: templates
    carry what the endpoint SAID, never what someone remembered."""
    try:
        url = base_url.rstrip("/") + "/models"
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            doc = json.loads(r.read().decode("utf-8"))
        items = doc.get("data") or doc.get("models") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            mid = item.get("id") or item.get("name") or item.get("model")
            if mid:
                return str(mid)
    except Exception:
        pass
    return "unknown"


def routing_template(model_id: str,
                     base_url: str = DEFAULT_LOCAL_ENDPOINT) -> dict:
    """The scaffolded routing policy, parameterized on the model id the
    endpoint actually reported (or "unknown" — an honest placeholder
    beats a confident lie)."""
    def cand(max_tokens: int, timeout_s: int) -> dict:
        return {
            "provider": "local",
            "model": model_id,
            "base_url": base_url,
            "max_tokens": max_tokens,
            "timeout_s": timeout_s,
            "cost_tier": "free",
            "local": True,
        }

    return {
        "prefer_local_when": {"tiers": ["reflex"]},
        "defaults": {
            "max_retries_same": 2,
            "backoff_base_s": 0.5,
            "min_content_chars": 1,
        },
        "tiers": {
            "reflex": {"candidates": [cand(1024, 60)]},
            "standard": {"candidates": [cand(4096, 120)]},
            "deep": {"candidates": [cand(8192, 300)]},
        },
    }

TELEGRAM_TEMPLATE = {
    "token_env": "ANIMA_TELEGRAM_TOKEN",
    "allowed_chat_ids": [],
    "person_map": {},
    "operator_person": "operator",
}


def _http_template() -> dict:
    # The HTTP sense is the universal adapter and it REQUIRES a bearer
    # token — so init generates one, or `init → run --http` dies on the
    # launch pad (the 2026-08 crash-loop lesson: a scaffold that can't
    # boot the body it scaffolds is a trap, not a template). Loopback
    # bind by default, deliberately: HTTP callers are local peripherals
    # until the operator says otherwise.
    import secrets
    return {
        "port": 8760,
        "bind": "127.0.0.1",
        "token": secrets.token_urlsafe(24),
    }


def _web_template(auth: str = "open") -> dict:
    # Home-mode default (owner decision 2026-07-22): LAN-exposed AND
    # open — if a person can reach the home network, they can connect
    # and say hello to the agent. An Observatory nobody can reach is a
    # window with the curtains nailed shut. `anima init --auth token`
    # (or "auth": "token" + a token in web.json) restores the gate;
    # "bind": "127.0.0.1" goes loopback-only.
    cfg = {
        "port": 8762,
        "bind": "0.0.0.0",
        "auth": auth,
        "operator_person": "operator",
    }
    if auth == "token":
        import secrets
        cfg["token"] = secrets.token_urlsafe(24)
    return cfg

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
        json.dump(routing_template(probe_endpoint_model()), f, indent=2)
        f.write("\n")
    tg_path = os.path.join(root, "senses", "telegram.json")
    if not os.path.exists(tg_path):
        with open(tg_path, "w", encoding="utf-8") as f:
            json.dump(TELEGRAM_TEMPLATE, f, indent=2)
            f.write("\n")
    web_path = os.path.join(root, "senses", "web.json")
    if not os.path.exists(web_path):
        with open(web_path, "w", encoding="utf-8") as f:
            json.dump(_web_template(getattr(args, "auth", "open")),
                      f, indent=2)
            f.write("\n")
    http_path = os.path.join(root, "senses", "http.json")
    if not os.path.exists(http_path):
        with open(http_path, "w", encoding="utf-8") as f:
            json.dump(_http_template(), f, indent=2)
            f.write("\n")
        os.chmod(http_path, 0o600)  # it holds a bearer token

    # Let EntityRoot assemble the organs once: creates the sqlite stores
    # and writes the "init" lineage entry (the birth certificate line).
    from .entity import EntityRoot
    entity = EntityRoot(root)
    entity.close()

    # Phase 8a (reach): the VAPID keypair is identity — the entity
    # signs its own pushes — and the icon is its face mark. Grown at
    # birth; regrown lazily by the web sense if ever deleted.
    from .runtime.pwa import ensure_icons, ensure_vapid_keys
    ensure_vapid_keys(root)
    ensure_icons(root)

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
    if getattr(args, "tls", False):
        argv.append("--tls")
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


# ── backup ────────────────────────────────────────────────────────────────────

def cmd_backup(args) -> int:
    from .backup import cmd_backup as _backup
    return _backup(args)


# ── doctor ────────────────────────────────────────────────────────────────────

def cmd_doctor(args) -> int:
    from .doctor import cmd_doctor as _doctor
    return _doctor(args)


# ── graph (Phase 7: rot resistance) ───────────────────────────────────────────

def cmd_graph(args) -> int:
    from .memory.graph import graph_gc
    from .memory.store import MemoryStore
    root = os.path.abspath(args.root)
    if not os.path.exists(os.path.join(root, "identity", "lineage.log")):
        print(f"no entity root at {root}", file=sys.stderr)
        return 1
    with MemoryStore(root) as store:
        if args.verb == "stats":
            print(json.dumps(store.graph_stats(), indent=2))
            return 0
        report = graph_gc(store,
                          prune_threshold=args.threshold,
                          half_life_days=args.half_life_days)
    if getattr(args, "json", False):
        print(json.dumps(report))
    else:
        print(f"graph gc: pruned {report['pruned_edges']} edge(s), "
              f"merged {report['merged_stubs']} duplicate stub(s)")
        print(f"now: {report['nodes']} nodes · {report['edges']} edges "
              f"· {report['stubs']} stubs · {report['orphans']} orphans")
    return 0


# ── sky (the shared sky: multi-entity observatory) ──────────────────

def _sky_template() -> dict:
    # Home-mode default: the sky page is open, and peers may omit
    # tokens entirely (open-mode Observatories need none). Add
    # "auth": "token" + a "token" here to gate the sky page; add a
    # "token" to a peer entry when THAT peer runs in token mode.
    return {
        "port": 8763,
        "bind": "0.0.0.0",
        "auth": "open",
        "poll_s": 10,
        "timeout_s": 4,
        "title": "the shared sky",
        "peers": [
            {"name": "example",
             "url": "http://127.0.0.1:8762"},
        ],
    }


def cmd_sky(args) -> int:
    cfg_path = os.path.abspath(args.config)

    if args.init:
        if os.path.exists(cfg_path):
            print(f"refusing to overwrite existing sky config at "
                  f"{cfg_path}", file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(cfg_path) or ".", exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(_sky_template(), f, indent=2)
            f.write("\n")
        print(f"sky config scaffolded at {cfg_path}")
        print("edit the peers list (each peer's Observatory URL + web "
              "token), then: anima sky --config " + args.config)
        return 0

    from .runtime.sky import SkyAggregator
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print(f"no sky config at {cfg_path}; scaffold one with "
              f"`anima sky --config {args.config} --init`",
              file=sys.stderr)
        return 1
    if args.port is not None:
        cfg["port"] = args.port
    if args.bind is not None:
        cfg["bind"] = args.bind

    sky = SkyAggregator(cfg)
    sky.refresh()                      # first pass before serving
    sky.start()
    from .runtime.__main__ import _lan_ip
    host = sky.bind if sky.bind not in ("0.0.0.0", "") else _lan_ip()
    up = sum(1 for p in sky._snapshot["peers"] if p["reachable"])
    if sky.auth == "open":
        print(f"shared sky: http://{host}:{sky.port}/  (open to the "
              f"LAN — anyone who can reach it sees it)",
              file=sys.stderr)
    else:
        print(f"shared sky: http://{host}:{sky.port}/?token=… "
              f"(token in {cfg_path})", file=sys.stderr)
    print(f"peers: {up}/{len(sky.peers)} reachable", file=sys.stderr)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        sky.stop()
    return 0


# ── service (systemd user unit: boot + crash resiliency) ──────────

def cmd_service(args) -> int:
    from .service import ServiceManager
    mgr = ServiceManager()
    try:
        if args.verb == "install":
            return mgr.install(args.root, name=args.name,
                               web=not args.no_web,
                               telegram=args.telegram,
                               tls=getattr(args, "tls", False),
                               force=args.force)
        if args.verb == "status":
            return mgr.status(args.root, name=args.name)
        if args.verb == "stop":
            return mgr.stop(args.root, name=args.name)
        if args.verb == "restart":
            return mgr.restart(args.root, name=args.name)
        if args.verb == "uninstall":
            return mgr.uninstall(args.root, name=args.name)
        raise AssertionError(args.verb)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


# ── entry point ───────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="anima",
        description="ANIMA — the agent is the artifact.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold a new entity root")
    p.add_argument("root", help="directory to become the entity root")
    p.add_argument("--auth", choices=("open", "token"), default="open",
                   help="Observatory auth mode for the scaffolded "
                        "web.json (default: open — anyone on the LAN; "
                        "token generates a random access token)")
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
    p.add_argument("--tls", action="store_true",
                   help="serve the Observatory over HTTPS with a "
                        "self-signed cert (identity/tls/; generated "
                        "via openssl on first use) — required for "
                        "push on iOS")
    p.add_argument("--sender", default=None,
                   help="person id for console messages")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="inspect an entity root")
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("backup",
                       help="timestamped tar.gz snapshot of an entity "
                            "root (live-safe; sqlite via backup API)")
    p.add_argument("--root", required=True, help="entity root directory")
    p.add_argument("--dest", default=None,
                   help="destination directory (default: "
                        "<root>/../anima-backups/<rootname>/)")
    p.add_argument("--keep", type=int, default=14,
                   help="newest archives to retain after pruning "
                        "(default 14)")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("doctor",
                       help="read-only preflight checks on an entity "
                            "root (PASS/WARN/FAIL)")
    p.add_argument("--root", required=True, help="entity root directory")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("graph",
                       help="graph maintenance: gc prunes decayed "
                            "edges and merges duplicate stubs; stats "
                            "prints counts")
    p.add_argument("verb", choices=("gc", "stats"))
    p.add_argument("--root", required=True, help="entity root directory")
    p.add_argument("--threshold", type=float, default=0.05,
                   help="prune edges whose weight × age-decay falls "
                        "below this (default 0.05)")
    p.add_argument("--half-life-days", type=float, default=90.0,
                   help="edge age-decay half-life (default 90)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("sync", help="MIGRATE an entity root (forks diverge)")
    p.add_argument("root", help="source entity root")
    p.add_argument("dest", help="destination directory (must be empty)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("sky", help="serve the shared sky (multi-entity "
                                   "observatory aggregator)")
    p.add_argument("--config", required=True,
                   help="sky config JSON (e.g. senses/sky.json)")
    p.add_argument("--init", action="store_true",
                   help="scaffold a sky config template and exit")
    p.add_argument("--port", type=int, default=None,
                   help="override the configured port")
    p.add_argument("--bind", default=None,
                   help="override the configured bind address")
    p.set_defaults(func=cmd_sky)

    p = sub.add_parser("service",
                       help="run the entity as a systemd user service "
                            "(auto-start at boot, auto-restart on "
                            "crash)")
    p.add_argument("verb", choices=("install", "status", "stop",
                                    "restart", "uninstall"))
    p.add_argument("--root", required=True, help="entity root directory")
    p.add_argument("--name", default=None,
                   help="service name (default: the root's basename "
                        "→ anima-<name>.service)")
    p.add_argument("--no-web", action="store_true",
                   help="install without the Observatory web GUI "
                        "(web is ON by default — it's the point)")
    p.add_argument("--telegram", action="store_true",
                   help="also attach the Telegram sense")
    p.add_argument("--tls", action="store_true",
                   help="serve the Observatory over HTTPS "
                        "(self-signed; required for push on iOS)")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing unit file on install")
    p.set_defaults(func=cmd_service)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
