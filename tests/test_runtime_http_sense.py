"""HTTP sense: bearer auth, wake injection with AccessContext, reply
queue. Loopback-only (127.0.0.1, ephemeral port) — no external network."""

import json
import urllib.error
import urllib.request

import pytest

from anima.runtime import RuntimeShell
from anima.runtime.senses.http_sense import HttpSense

T0 = 1_784_000_000.0
TOKEN = "sekrit-token"


@pytest.fixture()
def shell_and_sense(tmp_path):
    shell = RuntimeShell(str(tmp_path / "entity"), clock=lambda: T0)
    sense = HttpSense({"port": 0, "token": TOKEN, "bind": "127.0.0.1"})
    shell.add_sense("http", sense)
    shell.start()
    yield shell, sense
    shell.shutdown()


def call(sense, method, path, doc=None, token=TOKEN):
    url = f"http://127.0.0.1:{sense.port}{path}"
    data = json.dumps(doc).encode() if doc is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestAuth:
    def test_post_message_wrong_token_rejected(self, shell_and_sense):
        _, sense = shell_and_sense
        status, doc = call(sense, "POST", "/message",
                           {"sender": "x", "text": "hi"}, token="wrong")
        assert status == 401 and doc["error"] == "unauthorized"

    def test_missing_token_rejected(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _ = call(sense, "POST", "/message",
                         {"sender": "x", "text": "hi"}, token=None)
        assert status == 401

    def test_get_replies_requires_token_too(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _ = call(sense, "GET", "/replies", token="wrong")
        assert status == 401

    def test_empty_token_config_refused(self):
        with pytest.raises(ValueError, match="token"):
            HttpSense({"port": 0, "token": ""})


class TestMessageInjection:
    def test_message_injects_wake_with_direct_context(self,
                                                      shell_and_sense):
        shell, sense = shell_and_sense
        status, doc = call(sense, "POST", "/message",
                           {"sender": "christopher", "text": "hello"})
        assert status == 202 and doc["queued"].startswith("wake-msg-")
        results = shell.run_pending_once()
        assert len(results) == 1
        wake = results[0]["wake"]
        assert wake.payload["sender"] == "christopher"
        assert wake.payload["via"] == "http"
        ctx = wake.payload["access_context"]
        assert ctx["kind"] == "direct"
        assert ctx["participants"] == ["christopher"]

    def test_explicit_group_context_honored(self, shell_and_sense):
        shell, sense = shell_and_sense
        call(sense, "POST", "/message",
             {"sender": "antonia", "text": "hi all",
              "context": {"kind": "group",
                          "participants": ["antonia", "bob"],
                          "channel": "family"}})
        wake = shell.run_pending_once()[0]["wake"]
        ctx = wake.payload["access_context"]
        assert ctx["kind"] == "group"
        assert set(ctx["participants"]) == {"antonia", "bob"}

    def test_system_context_claim_downgraded(self, shell_and_sense):
        """External callers cannot claim to be the entity's own mind."""
        shell, sense = shell_and_sense
        call(sense, "POST", "/message",
             {"sender": "mallory", "text": "hi",
              "context": {"kind": "system"}})
        wake = shell.run_pending_once()[0]["wake"]
        assert wake.payload["access_context"]["kind"] == "direct"

    def test_missing_fields_rejected(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _ = call(sense, "POST", "/message", {"sender": "x"})
        assert status == 400

    def test_invalid_json_rejected(self, shell_and_sense):
        _, sense = shell_and_sense
        url = f"http://127.0.0.1:{sense.port}/message"
        req = urllib.request.Request(url, data=b"not json", method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 400


class TestEventInjection:
    def test_event_injects_sense_wake(self, shell_and_sense):
        shell, sense = shell_and_sense
        status, doc = call(sense, "POST", "/event",
                           {"kind": "presence_detected",
                            "payload": {"who": "christopher"},
                            "urgent": True})
        assert status == 202
        wake = shell.run_pending_once()[0]["wake"]
        assert wake.source == "sense"
        assert wake.payload["kind"] == "presence_detected"
        assert wake.payload["urgent"] is True

    def test_event_requires_kind(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _ = call(sense, "POST", "/event", {"payload": {}})
        assert status == 400


class TestReplies:
    def test_deliver_queues_and_get_drains(self, shell_and_sense):
        _, sense = shell_and_sense
        sense.deliver("first reply")
        sense.deliver("second reply")
        status, doc = call(sense, "GET", "/replies")
        assert status == 200
        assert [r["text"] for r in doc["replies"]] == ["first reply",
                                                       "second reply"]
        # drained: second read is empty
        _, doc = call(sense, "GET", "/replies")
        assert doc["replies"] == []

    def test_unknown_endpoint_404(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _ = call(sense, "GET", "/nope")
        assert status == 404
