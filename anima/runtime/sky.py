"""The shared sky (Observatory v3b) — a multi-entity observatory.

One aggregator process observes SEVERAL running entities at once by
proxying read-only status from each peer's existing Observatory API.
The page it serves is a single shared constellation: every entity is a
star cluster (its lineage), pulsing with its drive heat; migration
edges — the lineage machinery's whole point — draw lines BETWEEN
clusters, making many biographies one sky.

Pure stdlib, same discipline as the web sense.

Config (senses/sky.json, or a dict):

    {"port": 8763,
     "bind": "0.0.0.0",
     "token": "<sky access token>",       # REQUIRED — the page's gate
     "poll_s": 10,                        # peer refresh cadence
     "timeout_s": 4,                      # per-peer request timeout
     "title": "the shared sky",
     "peers": [
       {"name": "luna",                   # optional; defaults to the
                                          #   peer's reported name
        "url": "http://host:8762",        # peer Observatory base URL
        "token": "<that peer's token>"}   # SERVER-SIDE ONLY
     ]}

Security model:
- The sky page has its OWN token (cookie/bearer/query, same
  browser-shaped scheme as the web sense).
- Peer tokens live in this config and are used server-side to poll the
  peers; they are NEVER included in /api/sky responses or the page.
  The peer `url` IS shipped (the cluster card links to that entity's
  own Observatory), so a sky viewer still needs that peer's token to
  get past its lock page — observing the sky grants no new authority.
- Peer-supplied content is re-sanitized HERE before serving (markup
  through the whitelist sanitizer, tones through the tone schema): a
  compromised peer cannot inject into the sky page.
- Unreachable peers degrade gracefully: a dim star with an `error`
  note, never a broken page.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import cookies as http_cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from .observatory import LOCK_PAGE, render_sky_page
from .sanitize import resanitize_expression

DEFAULT_SKY_PORT = 8763
_COOKIE = "anima_sky"
_LINEAGE_CAP = 48          # entries per peer shipped to the page
_MIGRATION_RE = re.compile(
    r"from\s+(?:[^\s:]+:)?(?P<src>/\S+)\s*->\s*(?P<dest>/\S+)")


def _parse_lineage(lines: list) -> list[dict]:
    parsed = []
    for line in lines:
        bits = [b.strip() for b in str(line).split("|", 2)]
        while len(bits) < 3:
            bits.append("")
        parsed.append({"ts": bits[0], "kind": bits[1], "detail": bits[2]})
    return parsed


class SkyAggregator:
    """Polls peer Observatories, serves the shared-sky page + JSON."""

    def __init__(self, config: Optional[dict] = None, *,
                 config_path: Optional[str] = None):
        if config is None:
            if not config_path or not os.path.exists(config_path):
                raise ValueError(
                    "SkyAggregator needs a config dict or an existing "
                    "config_path (senses/sky.json)")
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        self.token = str(config.get("token") or "")
        if not self.token:
            raise ValueError("sky config requires a non-empty token")
        self.port = int(config.get("port", DEFAULT_SKY_PORT))
        self.bind = str(config.get("bind", "0.0.0.0"))
        self.poll_s = float(config.get("poll_s", 10.0))
        self.timeout_s = float(config.get("timeout_s", 4.0))
        self.title = str(config.get("title") or "the shared sky")
        self.peers = []
        for i, p in enumerate(config.get("peers") or []):
            url = str(p.get("url") or "").rstrip("/")
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError(f"peer {i}: url must be http(s)")
            self.peers.append({
                "name": str(p.get("name") or "") or None,
                "url": url,
                "token": str(p.get("token") or ""),
            })
        if not self.peers:
            raise ValueError("sky config requires at least one peer")

        self.server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._poller: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot: dict = {"peers": [], "edges": [],
                                "fetched_at": None}

    # ── peer polling (server-side; peer tokens never leave here) ─────
    def _fetch(self, peer: dict, path: str) -> Any:
        req = urllib.request.Request(
            peer["url"] + path,
            headers={"Authorization": f"Bearer {peer['token']}",
                     "User-Agent": "anima-sky/0.1"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read().decode("utf-8"))

    def _poll_peer(self, peer: dict) -> dict:
        doc = {"name": peer["name"], "url": peer["url"],
               "reachable": False, "last_seen": None, "error": None,
               "stats": None, "drives": [], "lineage": [],
               "expression": None}
        try:
            stats = self._fetch(peer, "/api/stats")
            drives = self._fetch(peer, "/api/drives")
            lineage = self._fetch(peer, "/api/lineage")
            exprs = self._fetch(peer, "/api/expressions?limit=1")
            doc["reachable"] = True
            doc["last_seen"] = time.time()
            doc["name"] = peer["name"] or str(
                stats.get("name") or urllib.parse.urlsplit(
                    peer["url"]).netloc)
            mem = stats.get("memory") or {}
            doc["stats"] = {
                "episodes": mem.get("episodes"),
                "beliefs": (mem.get("beliefs") or {}).get("active"),
                "wakes": stats.get("wakes_dispatched"),
                "ledger": stats.get("ledger_entries"),
                "uptime_s": stats.get("uptime_s"),
                "lock": stats.get("lock"),
            }
            doc["drives"] = [
                {"name": d.get("name"),
                 "pressure": d.get("pressure"),
                 "fraction": d.get("fraction"),
                 "pending": bool(d.get("pending"))}
                for d in (drives.get("drives") or [])]
            entries = lineage.get("lineage") or []
            # peers may serve parsed dicts or raw lines
            if entries and isinstance(entries[0], str):
                entries = _parse_lineage(entries)
            doc["lineage"] = entries[-_LINEAGE_CAP:]
            rows = exprs.get("expressions") or []
            if rows:
                row = dict(rows[0])
                # trust boundary: re-sanitize peer content locally
                resanitize_expression(row)
                doc["expression"] = {
                    "kind": row.get("kind"), "title": row.get("title"),
                    "ts": row.get("ts"), "body": row.get("body")}
        except Exception as exc:
            doc["error"] = f"{type(exc).__name__}: {exc}"[:200]
            # keep the previous name if we ever resolved one
            with self._lock:
                for old in self._snapshot["peers"]:
                    if old["url"] == peer["url"]:
                        doc["name"] = old["name"]
                        doc["last_seen"] = old.get("last_seen")
                        break
        if not doc["name"]:
            doc["name"] = urllib.parse.urlsplit(peer["url"]).netloc
        return doc

    @staticmethod
    def _edges(peers: list[dict]) -> list[dict]:
        """Migration lineage entries that connect two known clusters.
        `anima sync` records `migrated from host:/src -> /dest` on the
        source BEFORE copying, so both forks carry the record; we match
        the path basenames against peer names, either direction."""
        by_name = {p["name"]: p for p in peers if p["name"]}
        edges, seen = [], set()
        for p in peers:
            for entry in p.get("lineage") or []:
                if entry.get("kind") != "migration":
                    continue
                m = _MIGRATION_RE.search(entry.get("detail") or "")
                if not m:
                    continue
                src = os.path.basename(m.group("src").rstrip("/"))
                dest = os.path.basename(m.group("dest").rstrip("/"))
                # both endpoints must be clusters in THIS sky; the
                # record itself may live on either fork (anima sync
                # writes it on the source before copying, so the copy
                # carries it too — dedup by (from, to, ts)).
                a, b = src, dest
                if a not in by_name or b not in by_name or a == b:
                    continue
                key = (a, b, entry.get("ts"))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"from": a, "to": b,
                              "ts": entry.get("ts"),
                              "detail": entry.get("detail")})
        return edges

    def refresh(self) -> dict:
        """One polling pass over all peers. Called by the poller
        thread; callable directly (tests, CLI warm-up)."""
        peers = [self._poll_peer(p) for p in self.peers]
        snapshot = {"peers": peers, "edges": self._edges(peers),
                    "fetched_at": time.time(), "title": self.title}
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                pass  # a bad pass must never kill the sky
            self._stop.wait(self.poll_s)

    # ── HTTP ──────────────────────────────────────────────────────────
    def start(self) -> None:
        sky = self
        self._stop.clear()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: N802
                pass

            def _split(self):
                parsed = urllib.parse.urlsplit(self.path)
                return parsed.path, urllib.parse.parse_qs(parsed.query)

            def _auth(self, query) -> tuple[bool, bool]:
                qtok = (query.get("token") or [None])[0]
                if qtok is not None:
                    return (qtok == sky.token, qtok == sky.token)
                header = self.headers.get("Authorization", "")
                if header == f"Bearer {sky.token}":
                    return True, False
                jar = http_cookies.SimpleCookie(
                    self.headers.get("Cookie", ""))
                morsel = jar.get(_COOKIE)
                return (bool(morsel) and morsel.value == sky.token,
                        False)

            def _send(self, code, body, ctype, set_cookie=False):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                if set_cookie:
                    self.send_header(
                        "Set-Cookie",
                        f"{_COOKIE}={sky.token}; HttpOnly; "
                        f"SameSite=Strict; Path=/")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, doc, set_cookie=False):
                self._send(code, json.dumps(doc).encode("utf-8"),
                           "application/json", set_cookie)

            def do_GET(self):  # noqa: N802
                path, query = self._split()
                authed, fresh = self._auth(query)
                if path == "/":
                    if not authed:
                        return self._send(401, LOCK_PAGE.encode("utf-8"),
                                          "text/html; charset=utf-8")
                    page = render_sky_page(sky.title)
                    return self._send(200, page.encode("utf-8"),
                                      "text/html; charset=utf-8",
                                      set_cookie=fresh)
                if path != "/api/sky":
                    return self._json(404, {"error": "unknown endpoint"})
                if not authed:
                    return self._json(401, {"error": "unauthorized"})
                with sky._lock:
                    snapshot = sky._snapshot
                return self._json(200, snapshot, set_cookie=fresh)

        self.server = ThreadingHTTPServer((self.bind, self.port), Handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
            name="anima-sky-http")
        self._thread.start()
        self._poller = threading.Thread(
            target=self._poll_loop, daemon=True, name="anima-sky-poll")
        self._poller.start()

    def stop(self) -> None:
        self._stop.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._poller is not None:
            self._poller.join(timeout=5)
            self._poller = None
