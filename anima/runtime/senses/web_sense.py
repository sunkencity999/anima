"""Web sense — the Observatory server (Phase 6b, ARCHITECTURE.md §6).

Serves the entity's face: a single-page dark-sky GUI (chat, expression
feed, drive gauges, lineage timeline, ledger stream, memory search).
Pure stdlib ThreadingHTTPServer, loopback by default, deliberately.

Config (senses/web.json inside the entity root, or a dict):

    {"port": 8762,                       # 0 = ephemeral (tests)
     "token": "<access token>",          # REQUIRED
     "bind": "127.0.0.1",
     "operator_person": "christopher"}   # who the chat panel speaks as

Auth model: browser-shaped. Hit any URL with ?token=<token> once → a
session cookie is set; from then on the cookie authenticates. All
/api/* endpoints and the page itself require it (401 + lock page
otherwise). Bearer <token> also works, for tests and curl.

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
import threading
import time
import urllib.parse
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
        if not self.token:
            raise ValueError("web sense config requires a non-empty token")
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
                """→ (authed, via_query_token). Cookie, bearer header,
                or ?token= all work; ?token= additionally earns the
                cookie so browsers only need it once."""
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

                return self._json(404, {"error": "unknown endpoint"})

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
