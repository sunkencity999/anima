"""Phase 7 §6 — introspection, not hardcoding.

Born from a screenshot: the Observatory confidently reporting a retired
model because a template had baked its name in. These tests pin the
standing rule — model ids come from asking the endpoint, never from
memory; host machinery panels come from config, never from assumption.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import anima.cli as cli
import anima.runtime.senses.web_sense as ws
from anima.runtime import RuntimeShell
from anima.runtime.senses.web_sense import WebSense

T0 = 1_784_000_000.0


@pytest.fixture(autouse=True)
def clear_model_cache():
    with ws._model_cache_lock:
        ws._model_cache.clear()
    yield
    with ws._model_cache_lock:
        ws._model_cache.clear()


def fake_models_server(model_id):
    """A tiny /v1/models endpoint that reports `model_id`."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps(
                {"data": [{"id": model_id}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


# ── anima init: the template asks, never assumes ─────────────────────

class TestInitTemplate:
    def test_probe_reads_real_model_id(self):
        srv, base = fake_models_server("Live-Model-7")
        try:
            assert cli.probe_endpoint_model(base) == "Live-Model-7"
        finally:
            srv.shutdown()

    def test_probe_dead_endpoint_returns_unknown(self):
        assert cli.probe_endpoint_model(
            "http://127.0.0.1:9/v1", timeout_s=0.3) == "unknown"

    def test_init_writes_probed_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "probe_endpoint_model",
                            lambda *a, **k: "Probed-Model-X")
        root = str(tmp_path / "ent")
        assert cli.main(["init", root]) == 0
        with open(os.path.join(root, "identity", "routing.json")) as f:
            routing = json.load(f)
        for tier in routing["tiers"].values():
            assert tier["candidates"][0]["model"] == "Probed-Model-X"

    def test_init_falls_back_to_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "probe_endpoint_model",
                            lambda *a, **k: "unknown")
        root = str(tmp_path / "ent")
        assert cli.main(["init", root]) == 0
        with open(os.path.join(root, "identity", "routing.json")) as f:
            routing = json.load(f)
        assert (routing["tiers"]["reflex"]["candidates"][0]["model"]
                == "unknown")

    def test_no_baked_in_model_names_anywhere(self):
        # The standing rule, greppable: nothing in anima/ names a model.
        pkg = os.path.dirname(cli.__file__)
        offenders = []
        for dirpath, dirnames, filenames in os.walk(pkg):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                with open(os.path.join(dirpath, fn),
                          encoding="utf-8") as f:
                    text = f.read()
                for needle in ("Qwen3-235B", "Qwen3-VL", "qwen3-235b",
                               "qwen3-vl-30b", "Qwen3-Coder"):
                    if needle in text:
                        offenders.append((fn, needle))
        assert not offenders


# ── reported-model cache ─────────────────────────────────────────────

class TestReportedModelCache:
    def test_reports_live_model(self):
        srv, base = fake_models_server("Cached-1")
        try:
            assert ws.reported_model_for(base) == "Cached-1"
        finally:
            srv.shutdown()

    def test_dead_endpoint_reports_none_and_caches(self, monkeypatch):
        calls = []

        def fake_tcp(url, timeout=0.5):
            calls.append(url)
            return False, None

        monkeypatch.setattr(ws, "_tcp_alive", fake_tcp)
        assert ws.reported_model_for("http://127.0.0.1:9/v1",
                                     now=T0) is None
        assert ws.reported_model_for("http://127.0.0.1:9/v1",
                                     now=T0 + 10) is None
        assert len(calls) == 1  # second answer came from the cache

    def test_cache_expires_after_ttl(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            ws, "_tcp_alive",
            lambda url, timeout=0.5: (calls.append(url), (False, None))[1])
        ws.reported_model_for("http://x/v1", now=T0)
        ws.reported_model_for("http://x/v1", now=T0 + 61)
        assert len(calls) == 2


# ── Observatory drift + tenants ──────────────────────────────────────

def call(sense, path):
    import urllib.request
    with urllib.request.urlopen(
            f"http://127.0.0.1:{sense.port}{path}", timeout=10) as r:
        return r.status, json.loads(r.read().decode())


@pytest.fixture()
def shell_root(tmp_path):
    return str(tmp_path / "ent")


def make_shell(root, sense):
    shell = RuntimeShell(root, clock=lambda: T0)
    shell.add_sense("web", sense)
    shell.start()
    return shell


def write_routing(root, model, base_url):
    ident = os.path.join(root, "identity")
    os.makedirs(ident, exist_ok=True)
    doc = {"tiers": {"reflex": {"candidates": [{
        "provider": "local", "model": model, "base_url": base_url}]}}}
    with open(os.path.join(ident, "routing.json"), "w") as f:
        json.dump(doc, f)


class TestDriftDisplay:
    def test_routing_status_carries_reported_model_and_drift(
            self, shell_root, monkeypatch):
        write_routing(shell_root, "Configured-A", "http://127.0.0.1:9/v1")
        monkeypatch.setattr(ws, "_tcp_alive",
                            lambda url, timeout=0.5: (True, 1.0))
        monkeypatch.setattr(ws, "reported_model_for",
                            lambda base, **kw: "Actual-B")
        sense = WebSense({"port": 0, "auth": "open",
                          "bind": "127.0.0.1"})
        shell = make_shell(shell_root, sense)
        try:
            _, doc = call(sense, "/api/routing")
            s = doc["candidates_status"][0]
            assert s["model"] == "Configured-A"
            assert s["reported_model"] == "Actual-B"
            assert s["drift"] is True
        finally:
            shell.shutdown()

    def test_matching_model_is_not_drift(self, shell_root, monkeypatch):
        write_routing(shell_root, "Same-1", "http://127.0.0.1:9/v1")
        monkeypatch.setattr(ws, "_tcp_alive",
                            lambda url, timeout=0.5: (True, 1.0))
        monkeypatch.setattr(ws, "reported_model_for",
                            lambda base, **kw: "Same-1")
        sense = WebSense({"port": 0, "auth": "open",
                          "bind": "127.0.0.1"})
        shell = make_shell(shell_root, sense)
        try:
            _, doc = call(sense, "/api/routing")
            assert doc["candidates_status"][0]["drift"] is False
        finally:
            shell.shutdown()

    def test_under_the_hood_model_reports(self, shell_root, monkeypatch):
        write_routing(shell_root, "Old-Name", "http://127.0.0.1:9/v1")
        monkeypatch.setattr(ws, "reported_model_for",
                            lambda base, **kw: "New-Name")
        sense = WebSense({"port": 0, "auth": "open",
                          "bind": "127.0.0.1"})
        shell = make_shell(shell_root, sense)
        try:
            _, doc = call(sense, "/api/under-the-hood")
            reps = doc["models"]
            assert reps and reps[0]["reported"] == "New-Name"
            assert reps[0]["configured"] == ["Old-Name"]
            assert reps[0]["drift"] is True
        finally:
            shell.shutdown()


class TestTenantsConfig:
    def test_no_tenants_config_means_empty_panel(self, shell_root):
        sense = WebSense({"port": 0, "auth": "open",
                          "bind": "127.0.0.1"})
        shell = make_shell(shell_root, sense)
        try:
            _, doc = call(sense, "/api/under-the-hood")
            assert doc["tenants"] == []
            # the old hardcoded keys are gone for good
            assert "services" not in doc
            assert "endpoints" not in doc
        finally:
            shell.shutdown()

    def test_http_tenant_probed_live(self, shell_root):
        sense = WebSense({
            "port": 0, "auth": "open", "bind": "127.0.0.1",
            "tenants": [{"label": "dead door", "kind": "http",
                         "url": "http://127.0.0.1:9"}]})
        shell = make_shell(shell_root, sense)
        try:
            _, doc = call(sense, "/api/under-the-hood")
            t = doc["tenants"][0]
            assert t["label"] == "dead door"
            assert t["state"] == "down" and t["alive"] is False
        finally:
            shell.shutdown()

    def test_malformed_tenant_entries_dropped(self, shell_root):
        sense = WebSense({
            "port": 0, "auth": "open", "bind": "127.0.0.1",
            "tenants": ["garbage", {"kind": "http"}, {"kind": "nope",
                                                      "url": "x"},
                        {"label": "ok", "kind": "http",
                         "url": "http://127.0.0.1:9"}]})
        assert len(sense.tenants) == 1

    def test_swap_marker_only_when_configured(self, shell_root,
                                              tmp_path):
        marker = tmp_path / "swap_marker"
        marker.write_text("")
        sense = WebSense({"port": 0, "auth": "open",
                          "bind": "127.0.0.1",
                          "swap_marker": str(marker)})
        shell = make_shell(shell_root, sense)
        try:
            _, doc = call(sense, "/api/under-the-hood")
            assert doc["swap_marker"]["present"] is True
        finally:
            shell.shutdown()
