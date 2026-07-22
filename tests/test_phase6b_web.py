"""Phase 6b web sense (the Observatory): auth, wake injection, ACL-walled
memory search, expression cards, page serving. Loopback-only
(127.0.0.1, ephemeral port) — fully offline."""

import json
import urllib.error
import urllib.request

import pytest

from anima.runtime import RuntimeShell
from anima.runtime.senses.web_sense import WebSense

T0 = 1_784_000_000.0
TOKEN = "obs-sekrit"


@pytest.fixture()
def shell_and_sense(tmp_path):
    shell = RuntimeShell(str(tmp_path / "luna"), clock=lambda: T0)
    sense = WebSense({"port": 0, "token": TOKEN, "bind": "127.0.0.1",
                      "operator_person": "christopher"})
    shell.add_sense("web", sense)
    shell.start()
    yield shell, sense
    shell.shutdown()


def call(sense, method, path, doc=None, *, token=TOKEN, cookie=None,
         raw=False):
    url = f"http://127.0.0.1:{sense.port}{path}"
    data = json.dumps(doc).encode() if doc is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if cookie is not None:
        req.add_header("Cookie", cookie)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            headers = dict(resp.headers)
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read()
        headers = dict(e.headers)
        status = e.code
    if raw:
        return status, body, headers
    return status, json.loads(body.decode()), headers


class TestAuth:
    def test_api_rejects_bad_token(self, shell_and_sense):
        _, sense = shell_and_sense
        status, doc, _ = call(sense, "GET", "/api/stats", token="wrong")
        assert status == 401 and doc["error"] == "unauthorized"

    def test_api_rejects_missing_token(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _, _ = call(sense, "GET", "/api/ledger", token=None)
        assert status == 401

    def test_query_token_sets_cookie_then_cookie_works(self,
                                                       shell_and_sense):
        _, sense = shell_and_sense
        status, body, headers = call(
            sense, "GET", f"/?token={TOKEN}", token=None, raw=True)
        assert status == 200
        set_cookie = headers.get("Set-Cookie", "")
        assert "anima_observatory=" in set_cookie
        cookie = set_cookie.split(";")[0]
        status, doc, _ = call(sense, "GET", "/api/stats", token=None,
                              cookie=cookie)
        assert status == 200 and "memory" in doc

    def test_wrong_query_token_rejected(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _, _ = call(sense, "GET", "/api/stats?token=nope",
                            token=None)
        assert status == 401

    def test_wrong_cookie_rejected(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _, _ = call(sense, "GET", "/api/stats", token=None,
                            cookie="anima_observatory=forged")
        assert status == 401

    def test_root_without_auth_serves_lock_page(self, shell_and_sense):
        _, sense = shell_and_sense
        status, body, _ = call(sense, "GET", "/", token=None, raw=True)
        assert status == 401
        assert b"DOME IS CLOSED" in body

    def test_empty_token_config_refused(self):
        with pytest.raises(ValueError, match="token"):
            WebSense({"port": 0, "token": ""})


class TestPage:
    def test_root_serves_observatory_html(self, shell_and_sense):
        _, sense = shell_and_sense
        status, body, headers = call(sense, "GET", "/", raw=True)
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        html = body.decode()
        assert "<!DOCTYPE html>" in html
        assert "luna" in html               # entity name from root dir
        assert "Observatory" in html
        assert "cdn" not in html.lower()    # no external anything
        assert "fonts.googleapis" not in html


class TestMessageAndReplies:
    def test_message_injects_operator_direct_wake(self, shell_and_sense):
        shell, sense = shell_and_sense
        status, doc, _ = call(sense, "POST", "/api/message",
                              {"text": "hello from the dome"})
        assert status == 202 and doc["queued"].startswith("wake-msg-")
        results = shell.run_pending_once()
        assert len(results) == 1
        wake = results[0]["wake"]
        assert wake.payload["sender"] == "christopher"
        assert wake.payload["via"] == "web"
        ctx = wake.payload["access_context"]
        assert ctx["kind"] == "direct"
        assert ctx["participants"] == ["christopher"]

    def test_message_requires_text(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _, _ = call(sense, "POST", "/api/message", {"text": " "})
        assert status == 400

    def test_message_requires_auth(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _, _ = call(sense, "POST", "/api/message",
                            {"text": "hi"}, token="wrong")
        assert status == 401

    def test_replies_drain(self, shell_and_sense):
        _, sense = shell_and_sense
        sense.deliver("the dome hums", None)
        status, doc, _ = call(sense, "GET", "/api/replies")
        assert status == 200
        assert [r["text"] for r in doc["replies"]] == ["the dome hums"]
        _, doc2, _ = call(sense, "GET", "/api/replies")
        assert doc2["replies"] == []


class TestMemorySearchACL:
    def test_private_rows_of_others_invisible(self, shell_and_sense):
        shell, sense = shell_and_sense
        store = shell.entity.store
        store.add_episode("antonia's secret garden plan", ts=T0,
                          scope="private", owner="antonia",
                          tags=["garden"])
        store.add_episode("christopher's secret garden gift", ts=T0,
                          scope="private", owner="christopher",
                          tags=["garden"])
        store.add_episode("shared note about the garden hose", ts=T0,
                          scope="shared", tags=["garden"])
        status, doc, _ = call(sense, "GET", "/api/memory/search?q=garden")
        assert status == 200
        summaries = " | ".join(e["summary"] for e in doc["episodes"])
        assert "christopher's secret" in summaries
        assert "shared note" in summaries
        assert "antonia's secret" not in summaries
        assert doc["as_person"] == "christopher"

    def test_beliefs_searched_too(self, shell_and_sense):
        shell, sense = shell_and_sense
        shell.entity.store.add_belief(
            "the telescope needs collimation", ts=T0)
        _, doc, _ = call(sense, "GET",
                         "/api/memory/search?q=telescope%20collimation")
        assert any("collimation" in b["statement"]
                   for b in doc["beliefs"])

    def test_empty_query_rejected(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _, _ = call(sense, "GET", "/api/memory/search?q=")
        assert status == 400


class TestPanels:
    def test_expressions_served_sanitized(self, shell_and_sense):
        shell, sense = shell_and_sense
        shell.entity.store.add_expression(
            '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>',
            kind="svg", title="a moon", ts=T0)
        status, doc, _ = call(sense, "GET", "/api/expressions?limit=5")
        assert status == 200
        card = doc["expressions"][0]
        assert card["title"] == "a moon"
        assert 'viewBox="0 0 10 10"' in card["body"]
        assert "script" not in card["body"]

    def test_lineage_parsed(self, shell_and_sense):
        _, sense = shell_and_sense
        status, doc, _ = call(sense, "GET", "/api/lineage")
        assert status == 200
        assert doc["lineage"], "init + shell_start expected"
        entry = doc["lineage"][0]
        assert set(entry) == {"ts", "kind", "detail"}
        kinds = {e["kind"] for e in doc["lineage"]}
        assert "init" in kinds

    def test_ledger_and_stats(self, shell_and_sense):
        shell, sense = shell_and_sense
        call(sense, "POST", "/api/message", {"text": "wake up"})
        shell.run_pending_once()
        _, ledger, _ = call(sense, "GET", "/api/ledger?limit=10")
        assert isinstance(ledger["actions"], list)
        _, stats, _ = call(sense, "GET", "/api/stats")
        assert stats["name"] == "luna"
        assert "lock" in stats and "memory" in stats
        assert stats["uptime_s"] >= 0

    def test_drives_endpoint_empty_without_config(self, shell_and_sense):
        _, sense = shell_and_sense
        status, doc, _ = call(sense, "GET", "/api/drives")
        assert status == 200 and doc["drives"] == []

    def test_drives_endpoint_with_drives(self, tmp_path):
        shell = RuntimeShell(
            str(tmp_path / "e2"), clock=lambda: T0,
            drives={"curiosity": {"rate_per_hour": 0.5, "threshold": 1.0,
                                  "description": "explore"}})
        sense = WebSense({"port": 0, "token": TOKEN,
                          "operator_person": "christopher"})
        shell.add_sense("web", sense)
        shell.start()
        try:
            _, doc, _ = call(sense, "GET", "/api/drives")
            assert doc["drives"][0]["name"] == "curiosity"
            assert "pressure" in doc["drives"][0]
        finally:
            shell.shutdown()

    def test_unknown_api_endpoint_404(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _, _ = call(sense, "GET", "/api/nope")
        assert status == 404


class TestStream:
    """SSE /api/stream: auth gate + event framing."""

    def test_stream_requires_auth(self, shell_and_sense):
        _, sense = shell_and_sense
        status, doc, _ = call(sense, "GET", "/api/stream", token="wrong")
        assert status == 401 and doc["error"] == "unauthorized"

    def test_stream_emits_named_events(self, tmp_path):
        shell = RuntimeShell(str(tmp_path / "sse"), clock=lambda: T0)
        sense = WebSense({"port": 0, "token": TOKEN,
                          "operator_person": "christopher",
                          "stream_poll_s": 0.05,
                          "stream_heartbeat_s": 0.1})
        shell.add_sense("web", sense)
        shell.start()
        try:
            sense.deliver("the dome hums", None)
            url = f"http://127.0.0.1:{sense.port}/api/stream"
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {TOKEN}")
            buf = b""
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert resp.status == 200
                assert resp.headers["Content-Type"].startswith(
                    "text/event-stream")
                import time as _t
                deadline = _t.time() + 8
                while _t.time() < deadline:
                    buf += resp.read(1024)
                    if (b"event: ledger" in buf
                            and b"event: stats" in buf
                            and b"event: replies" in buf
                            and b": beat" in buf):
                        break
            text = buf.decode()
            assert "event: ledger\ndata: " in text
            assert "event: stats\ndata: " in text
            assert "event: expressions\ndata: " in text
            assert "event: replies\ndata: " in text
            assert ": beat" in text            # heartbeat comment
            # frames are well-formed: data line parses as JSON
            for block in text.split("\n\n"):
                if block.startswith("event: "):
                    data = [ln for ln in block.split("\n")
                            if ln.startswith("data: ")][0]
                    json.loads(data[len("data: "):])
        finally:
            shell.shutdown()
