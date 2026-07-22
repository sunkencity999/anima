"""HTTP sense — the universal adapter (PHASE5_RUNTIME.md).

Anything that can speak HTTP can be a sense. Pure stdlib http.server.

Config (senses/http.json inside the entity root, or passed as a dict):

    {"port": 8760,                # 0 = ephemeral (tests)
     "token": "<bearer token>",   # REQUIRED; every request must carry it
     "bind": "127.0.0.1",         # loopback by default, deliberately
     "callback_url": "http://..." # optional: replies POSTed here
    }

Endpoints (all require `Authorization: Bearer <token>`):

    POST /message  {"sender": "...", "text": "...",
                    "context": {"kind": "direct|group|public",
                                "participants": [...], "channel": "..."}?}
        → injects a message wake with a proper AccessContext
          (default: direct with the sender). 202 {"queued": wake_id}.

    POST /event    {"kind": "...", "payload": {...}?, "urgent": bool?}
        → injects a sense wake. 202 {"queued": wake_id}.

    GET  /replies  → drains the outbound reply queue:
          {"replies": [{"text": "...", "wake_id": "..."}]}
        (long-poll-ish: callers poll; when callback_url is configured
         replies are POSTed there instead and this queue stays empty).

The sense only INJECTS — the shell's scheduler loop dispatches. That
keeps the single-writer discipline intact: HTTP threads never run wakes.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from ...relationships import AccessContext

_MAX_BODY = 256 * 1024


def _context_from(doc: Any, sender: str) -> AccessContext:
    if isinstance(doc, dict) and doc.get("kind"):
        kind = str(doc["kind"])
        participants = [str(p) for p in (doc.get("participants") or [])]
        channel = str(doc.get("channel") or "http")
        if kind == "direct":
            people = participants or [sender]
            return AccessContext.direct(people[0], channel=channel,
                                        extra_participants=people[1:])
        if kind == "group":
            return AccessContext.group(participants or [sender],
                                       channel=channel)
        if kind == "public":
            return AccessContext.public(channel=channel)
        # kind == "system" over HTTP is refused: an external caller does
        # not get to claim to be the entity's own mind.
    return AccessContext.direct(sender, channel="http")


class HttpSense:
    name = "http"

    def __init__(self, config: Optional[dict] = None, *,
                 config_path: Optional[str] = None):
        if config is None:
            if not config_path or not os.path.exists(config_path):
                raise ValueError(
                    "HttpSense needs a config dict or an existing "
                    "config_path (senses/http.json)")
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        self.token = str(config.get("token") or "")
        if not self.token:
            raise ValueError("http sense config requires a non-empty token")
        self.port = int(config.get("port", 8760))
        self.bind = str(config.get("bind", "127.0.0.1"))
        self.callback_url = config.get("callback_url") or None
        self.shell: Any = None
        self.server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._replies: list = []
        self._replies_lock = threading.Lock()

    # ── shell lifecycle hooks ─────────────────────────────────────────
    def start(self, shell: Any) -> None:
        self.shell = shell
        sense = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: N802 — silence
                pass

            def _authed(self) -> bool:
                header = self.headers.get("Authorization", "")
                return header == f"Bearer {sense.token}"

            def _send(self, code: int, doc: dict) -> None:
                body = json.dumps(doc).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> Optional[dict]:
                try:
                    n = min(int(self.headers.get("Content-Length", 0)),
                            _MAX_BODY)
                    doc = json.loads(self.rfile.read(n).decode("utf-8"))
                    return doc if isinstance(doc, dict) else None
                except (ValueError, json.JSONDecodeError):
                    return None

            def do_POST(self):  # noqa: N802
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                doc = self._read_json()
                if doc is None:
                    return self._send(400, {"error": "invalid JSON body"})
                if self.path == "/message":
                    sender = str(doc.get("sender") or "").strip()
                    text = str(doc.get("text") or "")
                    if not sender or not text:
                        return self._send(
                            400, {"error": "sender and text required"})
                    ctx = _context_from(doc.get("context"), sender)
                    wake = sense.shell.inject_message(
                        sender, text, context=ctx, via="http")
                    return self._send(202, {"queued": wake.wake_id})
                if self.path == "/event":
                    kind = str(doc.get("kind") or "").strip()
                    if not kind:
                        return self._send(400, {"error": "kind required"})
                    wake = sense.shell.inject_event(
                        kind, doc.get("payload") or {},
                        urgent=bool(doc.get("urgent")), via="http")
                    return self._send(202, {"queued": wake.wake_id})
                return self._send(404, {"error": "unknown endpoint"})

            def do_GET(self):  # noqa: N802
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                if self.path == "/replies":
                    with sense._replies_lock:
                        out, sense._replies = sense._replies, []
                    return self._send(200, {"replies": out})
                return self._send(404, {"error": "unknown endpoint"})

        self.server = ThreadingHTTPServer((self.bind, self.port), Handler)
        self.port = self.server.server_address[1]  # resolve port 0
        self._thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
            name="anima-http-sense")
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
        entry = {"text": text,
                 "wake_id": getattr(wake, "wake_id", None)}
        if self.callback_url:
            try:
                req = urllib.request.Request(
                    self.callback_url,
                    data=json.dumps(entry).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST")
                urllib.request.urlopen(req, timeout=10).read()
                return
            except Exception:
                pass  # fall through: queue it so nothing is lost
        with self._replies_lock:
            self._replies.append(entry)
