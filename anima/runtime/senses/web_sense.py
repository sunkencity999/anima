"""Web sense — the Observatory server (Phase 6b, ARCHITECTURE.md §6).

Serves the entity's face: a single-page dark-sky GUI (chat, expression
feed, drive gauges, lineage timeline, ledger stream, memory search).
Pure stdlib ThreadingHTTPServer, loopback by default, deliberately.

Config (senses/web.json inside the entity root, or a dict):

    {"port": 8762,                       # 0 = ephemeral (tests)
     "auth": "open",                     # "open" (default) | "token"
     "token": "<access token>",          # required when auth="token"
     "bind": "127.0.0.1",
     "operator_person": "christopher"}   # who the chat panel speaks as

Auth model (home-mode, owner decision 2026-07-22): **open by default**.
A home agent should greet anyone who can reach it on the LAN — open
mode authorizes every request as the operator person, sets no cookie,
and never shows the lock page. `"auth": "token"` restores the
browser-shaped gate: hit any URL with ?token=<token> once → a session
cookie is set; from then on the cookie authenticates. All /api/*
endpoints and the page itself require it (401 + lock page otherwise).
Bearer <token> also works, for tests and curl.

Back-compat: configs written before the `auth` key infer their mode —
a token present with no explicit `auth` means token mode; neither
present means open.

Endpoints:
    GET  /                    → the Observatory page
    POST /api/message {text}  → direct-context wake from operator_person
         (202 body also carries `recall`: the ACL-walled memory
         snippets the orient phase surfaces for this wake — the page's
         conversation marginalia)
    GET  /api/replies         → drain the outbound reply queue
    GET  /api/lineage         → parsed lineage entries
    GET  /api/drives          → live drive pressures
    GET  /api/ledger?limit=50 → recent ledger rows
    GET  /api/history?until=<epoch>&limit=50 → time travel: ledger
         window at/before `until` + drive pressures reconstructed for
         that moment + timeline bounds. Read-only, windowed on demand.
    GET  /api/memory/search?q=… → episodic+semantic hits through a
         DIRECT AccessContext for operator_person — the Phase 4 wall is
         applied INSIDE sqlite; other people's private rows are
         structurally invisible even to the operator's own GUI.
    GET  /api/expressions?limit=20 → expression cards (re-sanitized at
         serve time: defense in depth)
    GET  /api/stats           → entity stats + lock/uptime
    GET  /api/stream          → Server-Sent Events: pushes ledger /
         expressions / drives / stats / replies deltas as named SSE
         events, with `: beat` comment heartbeats (~15s). The page
         prefers this and falls back to polling if the stream drops.

Threading: handlers run on HTTP threads but every touch of the entity's
organs happens under the shell's dispatch lock — the single-writer
discipline (Phase 5) extends to readers of the same sqlite handles.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http import cookies as http_cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from ...relationships import AccessContext
from ...relationships.acl import compile_acl
from ...memory.recall import recall_items
from ...wake.orient import derive_query
from ..observatory import LOCK_PAGE, render_page
from ..sanitize import resanitize_expression

_MAX_BODY = 256 * 1024
_COOKIE = "anima_observatory"

DEFAULT_PORT = 8762

# Under-the-hood instrumentation (host machinery, not the entity):
# the GPU tenants the routing layer depends on, the swap proxy that
# absorbs model-swap windows, and the shared swap marker.
_UTH_SERVICES = (
    "llama-qwen3-235b-local.service",
    "llama-qwen3-vl-30b.service",
    "comfy-local.service",
    "local-llm-swap-proxy.service",
    "ollama.service",
)
_UTH_ENDPOINTS = {
    "swap_proxy": "http://127.0.0.1:8106",
    "upstream_235b": "http://127.0.0.1:8103",
    "vision_vl": "http://127.0.0.1:8105",
    "ollama": "http://127.0.0.1:11434",
}
_SWAP_MARKER = os.path.expanduser("~/.openclaw/state/gpu_swap_in_progress")


def _tcp_alive(base_url: str, timeout: float = 0.5):
    """TCP connect to base_url's host:port → (alive, latency_ms)."""
    try:
        parsed = urllib.parse.urlsplit(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        t0 = time.time()
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.time() - t0) * 1000, 1)
    except OSError:
        return False, None
    except Exception:
        return False, None


def _fetch_json(url: str, timeout: float = 0.5):
    """GET url and parse JSON; {"error": "unreachable"} on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"error": "unreachable"}


def _validate_routing(doc):
    """→ None if valid, else a human error string. Shape contract:
    dict with a `tiers` dict; each tier a dict with a `candidates`
    list; each candidate has provider/model/base_url strings."""
    if not isinstance(doc, dict):
        return "body must be a JSON object"
    tiers = doc.get("tiers")
    if not isinstance(tiers, dict):
        return 'missing or invalid "tiers" (must be an object)'
    for tname, tier in tiers.items():
        if not isinstance(tier, dict):
            return f'tier "{tname}" must be an object'
        cands = tier.get("candidates")
        if not isinstance(cands, list):
            return f'tier "{tname}" needs a "candidates" list'
        for i, c in enumerate(cands):
            if not isinstance(c, dict):
                return f'tier "{tname}" candidate {i} must be an object'
            for key in ("provider", "model", "base_url"):
                if not isinstance(c.get(key), str) or not c.get(key):
                    return (f'tier "{tname}" candidate {i}: '
                            f'"{key}" must be a non-empty string')
    return None


class WebSense:
    name = "web"

    def __init__(self, config: Optional[dict] = None, *,
                 config_path: Optional[str] = None):
        if config is None:
            if not config_path or not os.path.exists(config_path):
                raise ValueError(
                    "WebSense needs a config dict or an existing "
                    "config_path (senses/web.json)")
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        self.token = str(config.get("token") or "")
        self.auth = _resolve_auth(config.get("auth"), self.token,
                                  what="web sense config")
        self.port = int(config.get("port", DEFAULT_PORT))
        # Default matches the init template: LAN-exposed, token-gated.
        self.bind = str(config.get("bind", "0.0.0.0"))
        self.operator = str(config.get("operator_person") or "operator")
        # SSE tunables (overridable in config; tests shrink them)
        self.stream_poll_s = float(config.get("stream_poll_s", 2.0))
        self.stream_heartbeat_s = float(
            config.get("stream_heartbeat_s", 15.0))
        self.shell: Any = None
        self.server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._replies: list = []
        self._replies_lock = threading.Lock()
        self._started_ts: Optional[float] = None

    # ── shared helpers ────────────────────────────────────────────────
    def _entity_name(self) -> str:
        try:
            return os.path.basename(self.shell.entity.root) or "anima"
        except Exception:
            return "anima"

    def _locked(self, fn, *args, **kwargs):
        """Run an entity-touching callable under the shell's dispatch
        lock: HTTP threads never race the scheduler on sqlite."""
        with self.shell._dispatch_lock:
            return fn(*args, **kwargs)

    # ── shell lifecycle hooks ─────────────────────────────────────────
    def start(self, shell: Any) -> None:
        self.shell = shell
        self._started_ts = time.time()
        sense = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: N802 — silence
                pass

            # ── plumbing ─────────────────────────────────────────────
            def _split(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                return parsed.path, query

            def _auth(self, query) -> tuple[bool, bool]:
                """→ (authed, via_query_token). Open mode: everyone on
                the wire IS the operator — that's the point of a home
                agent. Token mode: cookie, bearer header, or ?token=
                all work; ?token= additionally earns the cookie so
                browsers only need it once."""
                if sense.auth == "open":
                    return True, False
                qtok = (query.get("token") or [None])[0]
                if qtok is not None:
                    return (qtok == sense.token, qtok == sense.token)
                header = self.headers.get("Authorization", "")
                if header == f"Bearer {sense.token}":
                    return True, False
                jar = http_cookies.SimpleCookie(
                    self.headers.get("Cookie", ""))
                morsel = jar.get(_COOKIE)
                return (bool(morsel) and morsel.value == sense.token,
                        False)

            def _send(self, code: int, body: bytes, ctype: str,
                      set_cookie: bool = False) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                if set_cookie:
                    self.send_header(
                        "Set-Cookie",
                        f"{_COOKIE}={sense.token}; HttpOnly; "
                        f"SameSite=Strict; Path=/")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, doc: dict,
                      set_cookie: bool = False) -> None:
                self._send(code, json.dumps(doc).encode("utf-8"),
                           "application/json", set_cookie)

            def _read_json(self) -> Optional[dict]:
                try:
                    n = min(int(self.headers.get("Content-Length", 0)),
                            _MAX_BODY)
                    doc = json.loads(self.rfile.read(n).decode("utf-8"))
                    return doc if isinstance(doc, dict) else None
                except (ValueError, json.JSONDecodeError):
                    return None

            # ── GET ──────────────────────────────────────────────────
            def do_GET(self):  # noqa: N802
                path, query = self._split()
                authed, fresh = self._auth(query)

                if path == "/":
                    if not authed:
                        return self._send(401,
                                          LOCK_PAGE.encode("utf-8"),
                                          "text/html; charset=utf-8")
                    page = render_page(sense._entity_name())
                    return self._send(200, page.encode("utf-8"),
                                      "text/html; charset=utf-8",
                                      set_cookie=fresh)

                if not path.startswith("/api/"):
                    return self._json(404, {"error": "unknown endpoint"})
                if not authed:
                    return self._json(401, {"error": "unauthorized"})

                if path == "/api/stream":
                    return self._stream(fresh)

                try:
                    return self._api_get(path, query, fresh)
                except Exception as exc:  # a broken panel ≠ a dead dome
                    return self._json(500, {"error": f"{type(exc).__name__}"
                                                     f": {exc}"})

            def _api_get(self, path, query, fresh):
                entity = sense.shell.entity

                if path == "/api/replies":
                    with sense._replies_lock:
                        out, sense._replies = sense._replies, []
                    return self._json(200, {
                        "replies": out,
                        "entity": sense._entity_name()}, set_cookie=fresh)

                if path == "/api/lineage":
                    lines = sense._locked(entity.lineage)
                    parsed = []
                    for line in lines:
                        bits = [b.strip() for b in line.split("|", 2)]
                        while len(bits) < 3:
                            bits.append("")
                        parsed.append({"ts": bits[0], "kind": bits[1],
                                       "detail": bits[2]})
                    return self._json(200, {"lineage": parsed})

                if path == "/api/drives":
                    drives = []
                    if entity.drives is not None:
                        drives = sense._locked(
                            entity.drives.pressure_summary,
                            sense.shell.clock())
                    return self._json(200, {"drives": drives})

                if path == "/api/ledger":
                    limit = _int_arg(query, "limit", 50, 1, 500)
                    rows = sense._locked(entity.ledger.recent, limit)
                    return self._json(200, {"actions": rows})

                if path == "/api/history":
                    # Time travel (Observatory v3): a read-only window
                    # of the past — ledger rows at/before `until` plus
                    # drive pressures reconstructed for that moment.
                    # Windowed on demand; the full ledger never ships.
                    now = time.time()
                    until = _float_arg(query, "until", now)
                    until = max(0.0, min(until, now))
                    limit = _int_arg(query, "limit", 50, 1, 500)
                    actions = sense._locked(entity.ledger.window,
                                            until, limit)
                    drives = []
                    if entity.drives is not None:
                        drives = sense._locked(
                            entity.drives.history_summary, until)
                    bounds = sense._locked(entity.ledger.bounds)
                    return self._json(200, {
                        "until": until, "now": now,
                        "actions": actions, "drives": drives,
                        "bounds": bounds})

                if path == "/api/memory/search":
                    q = (query.get("q") or [""])[0].strip()
                    if not q:
                        return self._json(400, {"error": "q required"})
                    ctx = AccessContext.direct(sense.operator,
                                               channel="observatory")
                    household = sense._locked(
                        entity.relationships.household_members)
                    acl = compile_acl(ctx, household)
                    episodes = sense._locked(
                        entity.store.search_episodes, q, 10, acl)
                    beliefs = sense._locked(
                        entity.store.search_beliefs, q, 10, False, acl)
                    return self._json(200, {"episodes": episodes,
                                            "beliefs": beliefs,
                                            "as_person": sense.operator})

                if path == "/api/expressions":
                    limit = _int_arg(query, "limit", 20, 1, 100)
                    rows = sense._locked(
                        entity.store.recent_expressions, limit)
                    for row in rows:  # defense in depth: re-sanitize
                        resanitize_expression(row)
                    return self._json(200, {"expressions": rows})

                if path == "/api/stats":
                    stats = sense._locked(entity.stats)
                    stats["name"] = sense._entity_name()
                    up = (time.time() - sense._started_ts
                          if sense._started_ts else 0.0)
                    stats["uptime_s"] = round(up, 1)
                    stats["lock"] = (f"live · pid {os.getpid()} · up "
                                     f"{_fmt_uptime(up)}")
                    return self._json(200, stats)

                if path == "/api/routing":
                    return self._routing_get()

                if path == "/api/under-the-hood":
                    return self._under_the_hood()

                return self._json(404, {"error": "unknown endpoint"})

            # ── under the hood: routing + host machinery ─────────────
            def _routing_path(self) -> str:
                return os.path.join(sense.shell.entity.root,
                                    "identity", "routing.json")

            def _routing_get(self):
                rp = self._routing_path()
                try:
                    with open(rp, "r", encoding="utf-8") as f:
                        routing = json.load(f)
                    mtime = os.path.getmtime(rp)
                except FileNotFoundError:
                    return self._json(404, {"error": "routing.json "
                                                     "not found",
                                            "path": rp})
                except (ValueError, OSError) as exc:
                    return self._json(500, {"error": f"unreadable "
                                                     f"routing.json: {exc}",
                                            "path": rp})
                status, seen = [], {}
                for tname, tier in (routing.get("tiers") or {}).items():
                    if not isinstance(tier, dict):
                        continue
                    for i, c in enumerate(tier.get("candidates") or []):
                        if not isinstance(c, dict):
                            continue
                        base = str(c.get("base_url") or "")
                        if base not in seen:       # ping each url once
                            seen[base] = _tcp_alive(base)
                        alive, lat = seen[base]
                        status.append({
                            "tier": tname, "index": i,
                            "provider": c.get("provider"),
                            "model": c.get("model"),
                            "base_url": base,
                            "alive": alive, "latency_ms": lat})
                return self._json(200, {"routing": routing,
                                        "candidates_status": status,
                                        "path": rp, "mtime": mtime})

            def _routing_post(self):
                doc = self._read_json()
                if doc is None:
                    return self._json(400, {"error": "invalid JSON body"})
                err = _validate_routing(doc)
                if err:
                    return self._json(400, {"error": err})
                rp = self._routing_path()
                try:
                    if os.path.exists(rp):     # timestamped backup first
                        stamp = time.strftime("%Y%m%d_%H%M%S")
                        bak = f"{rp}.bak-{stamp}"
                        with open(rp, "rb") as src, \
                                open(bak, "wb") as dst:
                            dst.write(src.read())
                    tmp = rp + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(doc, f, indent=2)
                        f.write("\n")
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, rp)        # atomic: readers never
                    #                            see a half-written file
                    return self._json(200, {"ok": True,
                                            "mtime": os.path.getmtime(rp)})
                except OSError as exc:
                    return self._json(500, {"error": f"write failed: "
                                                     f"{exc}"})

            def _under_the_hood(self):
                out: dict = {}
                # the swap proxy's own health doc, inlined
                out["swap_proxy_health"] = _fetch_json(
                    _UTH_ENDPOINTS["swap_proxy"] + "/_proxy/health")
                # GPU tenants (systemd user units)
                services = {}
                for unit in _UTH_SERVICES:
                    try:
                        r = subprocess.run(
                            ["systemctl", "--user", "is-active", unit],
                            capture_output=True, text=True, timeout=1)
                        services[unit] = (r.stdout.strip()
                                          or r.stderr.strip()
                                          or "unknown")
                    except Exception:
                        services[unit] = "unknown"
                out["services"] = services
                # the shared GPU-swap marker: a swap window is open
                marker = {"present": False, "age_seconds": None,
                          "path": _SWAP_MARKER}
                try:
                    st = os.stat(_SWAP_MARKER)
                    marker["present"] = True
                    marker["age_seconds"] = round(
                        time.time() - st.st_mtime, 1)
                except OSError:
                    pass
                out["gpu_swap_marker"] = marker
                # last ledger actions (fall back to the lineage log)
                tail = []
                try:
                    rows = sense._locked(
                        sense.shell.entity.ledger.recent, 8)
                    tail = [{"ts": r.get("ts"), "kind": r.get("kind"),
                             "detail": r.get("detail")} for r in rows]
                except Exception:
                    try:
                        lp = os.path.join(sense.shell.entity.root,
                                          "identity", "lineage.log")
                        with open(lp, "r", encoding="utf-8") as f:
                            for line in f.readlines()[-8:]:
                                bits = [b.strip() for b
                                        in line.split("|", 2)]
                                while len(bits) < 3:
                                    bits.append("")
                                tail.append({"ts": bits[0],
                                             "kind": bits[1],
                                             "detail": bits[2]})
                    except OSError:
                        pass
                out["ledger_tail"] = tail
                # the interesting local doors, and whether each answers
                endpoints = {}
                for name, url in _UTH_ENDPOINTS.items():
                    alive, lat = _tcp_alive(url)
                    endpoints[name] = {"url": url, "alive": alive,
                                       "latency_ms": lat}
                out["endpoints"] = endpoints
                return self._json(200, out)

            # ── SSE stream ───────────────────────────────────────────
            def _stream(self, fresh: bool) -> None:
                """Server-Sent Events. Named events per panel; only
                deltas after the initial snapshot. Pure stdlib: chunked
                writes on the handler socket, no Content-Length."""
                self.send_response(200)
                self.send_header("Content-Type",
                                 "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                if fresh:
                    self.send_header(
                        "Set-Cookie",
                        f"{_COOKIE}={sense.token}; HttpOnly; "
                        f"SameSite=Strict; Path=/")
                self.end_headers()

                def emit(event: str, doc: dict) -> None:
                    frame = (f"event: {event}\n"
                             f"data: {json.dumps(doc)}\n\n")
                    self.wfile.write(frame.encode("utf-8"))
                    self.wfile.flush()

                entity = sense.shell.entity
                last_ledger_id = -1
                last_drives = last_stats = None
                seen_expr: set = set()
                last_beat = time.time()
                first = True
                try:
                    while sense.server is not None:
                        # ledger (rows newest-first; "fresh" = new ids)
                        rows = sense._locked(entity.ledger.recent, 50)
                        fresh_ids = [r["id"] for r in rows
                                     if r["id"] > last_ledger_id]
                        if fresh_ids or first:
                            if rows:
                                last_ledger_id = max(
                                    last_ledger_id,
                                    max(r["id"] for r in rows))
                            emit("ledger", {
                                "actions": rows,
                                "fresh_ids": [] if first else fresh_ids})

                        # expressions (re-sanitized: defense in depth)
                        xs = sense._locked(
                            entity.store.recent_expressions, 20)
                        new_x = [x for x in xs
                                 if x["id"] not in seen_expr]
                        if new_x or first:
                            for x in xs:
                                resanitize_expression(x)
                            seen_expr.update(x["id"] for x in xs)
                            emit("expressions", {"expressions": xs,
                                                 "initial": first})

                        # drives (only on change)
                        drives = []
                        if entity.drives is not None:
                            drives = sense._locked(
                                entity.drives.pressure_summary,
                                sense.shell.clock())
                        blob = json.dumps(drives, sort_keys=True)
                        if blob != last_drives:
                            last_drives = blob
                            emit("drives", {"drives": drives})

                        # stats (only on change; uptime churn excluded)
                        stats = sense._locked(entity.stats)
                        stats["name"] = sense._entity_name()
                        blob = json.dumps(stats, sort_keys=True)
                        if blob != last_stats:
                            last_stats = blob
                            up = (time.time() - sense._started_ts
                                  if sense._started_ts else 0.0)
                            stats["uptime_s"] = round(up, 1)
                            stats["lock"] = (
                                f"live · pid {os.getpid()} · up "
                                f"{_fmt_uptime(up)}")
                            emit("stats", stats)

                        # replies (drain — mirrors /api/replies)
                        with sense._replies_lock:
                            out, sense._replies = sense._replies, []
                        if out:
                            emit("replies", {
                                "replies": out,
                                "entity": sense._entity_name()})

                        first = False
                        now = time.time()
                        if now - last_beat >= sense.stream_heartbeat_s:
                            self.wfile.write(b": beat\n\n")
                            self.wfile.flush()
                            last_beat = now
                        time.sleep(sense.stream_poll_s)
                except (BrokenPipeError, ConnectionResetError,
                        OSError, AttributeError):
                    # window closed, or the shell shut down under us
                    return

            # ── POST ─────────────────────────────────────────────────
            def do_POST(self):  # noqa: N802
                path, query = self._split()
                authed, fresh = self._auth(query)
                if not authed:
                    return self._json(401, {"error": "unauthorized"})
                if path == "/api/routing":
                    try:
                        return self._routing_post()
                    except Exception as exc:   # broken panel ≠ dead dome
                        return self._json(500, {
                            "error": f"{type(exc).__name__}: {exc}"})
                if path != "/api/message":
                    return self._json(404, {"error": "unknown endpoint"})
                doc = self._read_json()
                if doc is None:
                    return self._json(400, {"error": "invalid JSON body"})
                text = str(doc.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "text required"})
                ctx = AccessContext.direct(sense.operator,
                                           channel="observatory")
                wake = sense.shell.inject_message(
                    sense.operator, text, context=ctx, via="web")
                # Marginalia (Observatory v3): the same ACL-walled
                # recall the orient phase will run for this wake —
                # which memories surface while the entity composes.
                # Same wall as /api/memory/search: the operator's own
                # direct context, compiled INSIDE sqlite; other
                # people's private rows are structurally invisible.
                recall = {"episodes": [], "beliefs": []}
                try:
                    items = sense._locked(
                        recall_items,
                        sense.shell.entity.store, derive_query(wake),
                        max_items=6, now=sense.shell.clock(),
                        access_context=ctx,
                        relationships=sense.shell.entity.relationships)
                    recall["episodes"] = [
                        {"id": e["id"], "ts": e["ts"],
                         "summary": str(e["summary"])[:200],
                         "kind": e.get("kind", "event")}
                        for e in items["episodes"][:6]]
                    recall["beliefs"] = [
                        {"id": b["id"],
                         "statement": str(b["statement"])[:200],
                         "confidence": b.get("confidence", 0.0)}
                        for b in items["beliefs"][:4]]
                except Exception:
                    pass  # marginalia is garnish; the wake already queued
                return self._json(202, {"queued": wake.wake_id,
                                        "recall": recall},
                                  set_cookie=fresh)

        self.server = ThreadingHTTPServer((self.bind, self.port), Handler)
        self.port = self.server.server_address[1]  # resolve port 0
        self._thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
            name="anima-web-sense")
        self._thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.shell = None

    # ── outbound ──────────────────────────────────────────────────────
    def deliver(self, text: str, wake: Any = None) -> None:
        with self._replies_lock:
            self._replies.append({
                "text": text,
                "wake_id": getattr(wake, "wake_id", None),
                "ts": time.time(),
            })


def _resolve_auth(auth_field, token: str, *, what: str) -> str:
    """Resolve the auth mode with back-compat inference.

    Explicit "open"/"token" wins; a legacy config with a token and no
    `auth` key keeps token behavior; neither → open (home-mode
    default). Token mode without a token is a config error.
    """
    if auth_field is None:
        mode = "token" if token else "open"
    else:
        mode = str(auth_field).strip().lower()
        if mode not in ("open", "token"):
            raise ValueError(
                f'{what}: "auth" must be "open" or "token", '
                f'got {auth_field!r}')
    if mode == "token" and not token:
        raise ValueError(
            f'{what}: auth="token" requires a non-empty token')
    return mode


def _int_arg(query, name: str, default: int, lo: int, hi: int) -> int:
    try:
        val = int((query.get(name) or [default])[0])
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


def _float_arg(query, name: str, default: float) -> float:
    try:
        return float((query.get(name) or [default])[0])
    except (TypeError, ValueError):
        return default


def _fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
