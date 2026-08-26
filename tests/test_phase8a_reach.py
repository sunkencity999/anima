"""Phase 8a — reach. PWA shell assets, VAPID key lifecycle, push
subscriptions as relationship data, the notify tool, the web sense's
push endpoints, and the doctor's reach checks.

Everything offline: the push transport is a stub everywhere (never real
HTTP), the doctor's Observatory probe is injected, and the only sockets
opened are loopback listeners the tests themselves own.
"""

import json
import os
import socket
import stat
import struct

import pytest

from anima.entity import EntityRoot
from anima.relationships import AccessContext, RelationshipStore
from anima.runtime import TurnContext, default_registry
from anima.runtime import pwa
from anima.runtime.tools import NOTIFY_MAX_PER_SETTLE
from anima.wake.sources import Wake

T0 = 1_784_000_000.0

SUB_A = {"endpoint": "https://push.example/dev-a",
         "keys": {"p256dh": "BPa" + "a" * 84, "auth": "dGVzdC1hdXRo"}}
SUB_B = {"endpoint": "https://push.example/dev-b",
         "keys": {"p256dh": "BPb" + "b" * 84, "auth": "dGVzdC1hdXRo"}}


# ── PWA shell assets ──────────────────────────────────────────────────

class TestManifest:
    def test_manifest_carries_identity_and_maskable_icons(self):
        doc = pwa.build_manifest("luna")
        assert doc["name"] == "luna — Observatory"
        assert doc["short_name"] == "luna"
        assert doc["display"] == "standalone"
        assert doc["start_url"] == "/"
        sizes = {i["sizes"] for i in doc["icons"]}
        assert sizes == {"192x192", "512x512"}
        for icon in doc["icons"]:
            assert "maskable" in icon["purpose"]

    def test_render_manifest_is_valid_json(self):
        doc = json.loads(pwa.render_manifest("luna"))
        assert doc["theme_color"] == pwa.THEME_COLOR


class TestIcon:
    def test_icon_is_a_real_png_of_the_right_size(self):
        data = pwa.generate_icon_png(64)
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", data[16:24])
        assert (w, h) == (64, 64)

    def test_icon_is_deterministic(self):
        assert pwa.generate_icon_png(32) == pwa.generate_icon_png(32)

    def test_icon_size_bounds(self):
        with pytest.raises(ValueError):
            pwa.generate_icon_png(8)
        with pytest.raises(ValueError):
            pwa.generate_icon_png(2048)

    def test_ensure_icon_writes_once_and_reuses(self, tmp_path):
        root = str(tmp_path / "ent")
        p1 = pwa.ensure_icon(root, 192)
        first_mtime = os.stat(p1).st_mtime_ns
        p2 = pwa.ensure_icon(root, 192)
        assert p1 == p2
        assert os.stat(p2).st_mtime_ns == first_mtime  # not re-rendered

    def test_ensure_icon_rejects_foreign_sizes(self, tmp_path):
        with pytest.raises(ValueError):
            pwa.ensure_icon(str(tmp_path / "ent"), 256)


class TestServiceWorker:
    def test_rendered_worker_carries_the_name(self):
        sw = pwa.render_sw("luna")
        assert "luna" in sw
        assert "__NAME__" not in sw

    def test_name_is_sanitized_for_js(self):
        sw = pwa.render_sw('ev"il`${x}<script>')
        # the hostile name arrives with its teeth pulled: quotes,
        # backticks, template holes and tags are stripped from the
        # substitution (the template's own backticks are its business)
        for bad in ('ev"il', "il`$", "`${", "<script>"):
            assert bad not in sw
        assert "evil{x}script" in sw  # letters survive

    def test_worker_never_caches_the_api(self):
        sw = pwa.render_sw("luna")
        # the honesty contract, as served: /api/ rides the network and
        # navigations fall back to an offline page, not a cached shell
        assert '.startsWith("/api/")' in sw
        assert "UNREACHABLE" in sw
        assert "showNotification" in sw


class TestVapidLifecycle:
    def test_generated_once_private_key_0600(self, tmp_path):
        root = str(tmp_path / "ent")
        keys = pwa.ensure_vapid_keys(root)
        assert keys["private_key"] and keys["public_key"]
        priv = os.path.join(root, "identity", "vapid", "private_key")
        assert stat.S_IMODE(os.stat(priv).st_mode) == 0o600
        # idempotent: the identity does not change on the second call
        assert pwa.ensure_vapid_keys(root) == keys

    def test_load_returns_none_when_absent(self, tmp_path):
        assert pwa.load_vapid_keys(str(tmp_path / "empty")) is None


# ── push subscriptions are relationship data ──────────────────────────

@pytest.fixture()
def rel(tmp_path):
    r = RelationshipStore(str(tmp_path / "entity"), clock=lambda: T0)
    yield r
    r.close()


class TestSubscriptionStore:
    def test_unknown_person_cannot_hold_a_subscription(self, rel):
        with pytest.raises(KeyError):
            rel.add_push_subscription("nobody", SUB_A)
        assert rel.push_subscriptions("nobody") == []

    def test_add_list_and_ua_label(self, rel):
        rel.upsert_person("christopher")
        rec = rel.add_push_subscription("christopher", SUB_A,
                                        ua="iPhone Safari")
        assert rec["ua"] == "iPhone Safari"
        subs = rel.push_subscriptions("christopher")
        assert [s["endpoint"] for s in subs] == [SUB_A["endpoint"]]
        assert rel.get_person("christopher")["push_subscriptions"] == subs

    def test_multiple_devices_stack_same_device_replaces(self, rel):
        rel.upsert_person("christopher")
        rel.add_push_subscription("christopher", SUB_A, ua="phone")
        rel.add_push_subscription("christopher", SUB_B, ua="tablet")
        assert len(rel.push_subscriptions("christopher")) == 2
        rel.add_push_subscription("christopher", SUB_A, ua="phone v2")
        subs = rel.push_subscriptions("christopher")
        assert len(subs) == 2
        by_ep = {s["endpoint"]: s for s in subs}
        assert by_ep[SUB_A["endpoint"]]["ua"] == "phone v2"

    def test_validation_fails_closed(self, rel):
        rel.upsert_person("christopher")
        with pytest.raises(ValueError):
            rel.add_push_subscription("christopher", {"endpoint": ""})
        with pytest.raises(ValueError):
            rel.add_push_subscription(
                "christopher", {"endpoint": "ftp://x", "keys":
                                {"p256dh": "a", "auth": "b"}})
        with pytest.raises(ValueError):
            rel.add_push_subscription(
                "christopher", {"endpoint": "https://x",
                                "keys": {"p256dh": "a"}})
        assert rel.push_subscriptions("christopher") == []

    def test_remove_reports_honestly(self, rel):
        rel.upsert_person("christopher")
        rel.add_push_subscription("christopher", SUB_A)
        assert rel.remove_push_subscription(
            "christopher", SUB_A["endpoint"]) is True
        assert rel.remove_push_subscription(
            "christopher", SUB_A["endpoint"]) is False
        assert rel.push_subscriptions("christopher") == []

    def test_subscriptions_survive_reopen(self, rel, tmp_path):
        rel.upsert_person("christopher")
        rel.add_push_subscription("christopher", SUB_A)
        rel.close()
        r2 = RelationshipStore(str(tmp_path / "entity"), clock=lambda: T0)
        try:
            subs = r2.push_subscriptions("christopher")
            assert [s["endpoint"] for s in subs] == [SUB_A["endpoint"]]
        finally:
            r2.close()


# ── the notify tool ───────────────────────────────────────────────────

@pytest.fixture()
def entity(tmp_path):
    e = EntityRoot(str(tmp_path / "entity"), clock=lambda: T0)
    yield e
    e.close()


def make_ctx(entity, *, risk_cap="medium", actions=8):
    wake = Wake(wake_id="wake-8a", source="message", reason="test",
                payload={"sender": "christopher", "text": "hi",
                         "via": "console"},
                budget={"max_tokens": 4000, "max_actions": actions,
                        "risk_cap": risk_cap},
                ts=T0)
    return TurnContext(
        entity=entity, wake=wake,
        access_context=AccessContext.direct("christopher"),
        now=T0, actions_left=actions, risk_cap=risk_cap,
        log_action=entity.ledger.bind(wake, clock=lambda: T0))


class StubPush:
    """The injected transport: records every send, answers by plan."""

    def __init__(self, plan=None):
        self.calls = []
        self.plan = plan or {}

    def __call__(self, subscription, payload, vapid_keys):
        self.calls.append((subscription, payload, vapid_keys))
        return self.plan.get(subscription.get("endpoint"), 201), ""


def ready_entity(entity):
    """VAPID keys + a subscribed christopher on two devices."""
    pwa.ensure_vapid_keys(entity.root)
    entity.relationships.upsert_person("christopher")
    entity.relationships.add_push_subscription("christopher", SUB_A,
                                               ua="phone")
    entity.relationships.add_push_subscription("christopher", SUB_B,
                                               ua="tablet")


class TestNotifyTool:
    def test_offered_at_normal_cap_hidden_at_low(self):
        reg = default_registry(push_send=StubPush())
        names = {s["function"]["name"] for s in reg.schemas("normal")}
        assert "notify" in names
        low = {s["function"]["name"] for s in reg.schemas("low")}
        assert "notify" not in low

    def test_sends_encrypted_payload_to_every_device(self, entity):
        ready_entity(entity)
        stub = StubPush()
        reg = default_registry(push_send=stub)
        out = reg.execute("notify", {"person": "christopher",
                                     "title": "tide window",
                                     "body": "minus tide at 6:03",
                                     "url": "/"}, make_ctx(entity))
        assert out["ok"], out
        assert out["result"]["delivered"] == 2
        assert out["result"]["pruned"] == 0
        assert len(stub.calls) == 2
        doc = json.loads(stub.calls[0][1].decode("utf-8"))
        assert doc == {"title": "tide window",
                       "body": "minus tide at 6:03", "url": "/"}
        assert stub.calls[0][2]["public_key"]  # vapid keys rode along

    def test_dead_subscriptions_pruned_on_410(self, entity):
        ready_entity(entity)
        stub = StubPush(plan={SUB_A["endpoint"]: 410})
        reg = default_registry(push_send=stub)
        out = reg.execute("notify", {"person": "christopher",
                                     "title": "t", "body": "b"},
                          make_ctx(entity))
        assert out["result"] == {"person": "christopher", "devices": 2,
                                 "delivered": 1, "pruned": 1, "failed": 0}
        left = entity.relationships.push_subscriptions("christopher")
        assert [s["endpoint"] for s in left] == [SUB_B["endpoint"]]

    def test_non_2xx_counts_failed_without_pruning(self, entity):
        ready_entity(entity)
        stub = StubPush(plan={SUB_A["endpoint"]: 500})
        reg = default_registry(push_send=stub)
        out = reg.execute("notify", {"person": "christopher",
                                     "title": "t", "body": "b"},
                          make_ctx(entity))
        assert out["result"]["failed"] == 1
        assert len(entity.relationships.push_subscriptions(
            "christopher")) == 2

    def test_notify_rail_caps_pushes_per_settle(self, entity):
        ready_entity(entity)
        reg = default_registry(push_send=StubPush())
        ctx = make_ctx(entity, actions=NOTIFY_MAX_PER_SETTLE + 3)
        for _ in range(NOTIFY_MAX_PER_SETTLE):
            assert reg.execute("notify", {"person": "christopher",
                                          "title": "t", "body": "b"},
                               ctx)["ok"]
        out = reg.execute("notify", {"person": "christopher",
                                     "title": "t", "body": "b"}, ctx)
        assert not out["ok"]
        assert "budget" in out["error"]

    def test_unsubscribed_person_is_an_honest_error(self, entity):
        pwa.ensure_vapid_keys(entity.root)
        entity.relationships.upsert_person("christopher")
        reg = default_registry(push_send=StubPush())
        out = reg.execute("notify", {"person": "christopher",
                                     "title": "t", "body": "b"},
                          make_ctx(entity))
        assert not out["ok"]
        assert "no push subscriptions" in out["error"]

    def test_unknown_person_and_missing_vapid(self, entity):
        reg = default_registry(push_send=StubPush())
        out = reg.execute("notify", {"person": "stranger",
                                     "title": "t", "body": "b"},
                          make_ctx(entity))
        assert not out["ok"] and "unknown person" in out["error"]
        entity.relationships.upsert_person("christopher")
        entity.relationships.add_push_subscription("christopher", SUB_A)
        out = reg.execute("notify", {"person": "christopher",
                                     "title": "t", "body": "b"},
                          make_ctx(entity))
        assert not out["ok"] and "VAPID" in out["error"]

    def test_requires_person_title_body(self, entity):
        ready_entity(entity)
        reg = default_registry(push_send=StubPush())
        for args in ({"title": "t", "body": "b"},
                     {"person": "christopher", "body": "b"},
                     {"person": "christopher", "title": "t"}):
            assert not reg.execute("notify", args, make_ctx(entity))["ok"]


# ── the web sense's push endpoints + shell assets ─────────────────────

from anima.runtime import RuntimeShell               # noqa: E402
from anima.runtime.senses.web_sense import WebSense  # noqa: E402

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


def call(sense, method, path, doc=None, *, token=TOKEN, raw=False):
    import urllib.error
    import urllib.request
    url = f"http://127.0.0.1:{sense.port}{path}"
    data = json.dumps(doc).encode() if doc is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body, status = resp.read(), resp.status
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
        ctype = e.headers.get("Content-Type", "")
    if raw:
        return status, body, ctype
    return status, json.loads(body.decode()), ctype


class TestPwaAssets:
    def test_manifest_served_without_auth(self, shell_and_sense):
        _, sense = shell_and_sense
        status, body, ctype = call(sense, "GET", "/manifest.webmanifest",
                                   token=None, raw=True)
        assert status == 200
        assert "manifest" in ctype
        doc = json.loads(body.decode())
        assert doc["display"] == "standalone"

    def test_sw_served_without_auth(self, shell_and_sense):
        _, sense = shell_and_sense
        status, body, ctype = call(sense, "GET", "/sw.js",
                                   token=None, raw=True)
        assert status == 200
        assert "javascript" in ctype
        assert b"showNotification" in body

    def test_icons_served_without_auth(self, shell_and_sense):
        _, sense = shell_and_sense
        for path in ("/icon-192.png", "/icon-512.png"):
            status, body, ctype = call(sense, "GET", path,
                                       token=None, raw=True)
            assert status == 200
            assert ctype == "image/png"
            assert body.startswith(b"\x89PNG\r\n\x1a\n")

    def test_vapid_keys_grown_at_sense_start(self, shell_and_sense):
        shell, _ = shell_and_sense
        assert pwa.load_vapid_keys(shell.entity.root) is not None

    def test_page_links_the_shell(self, shell_and_sense):
        _, sense = shell_and_sense
        status, body, _ = call(sense, "GET", f"/?token={TOKEN}",
                               token=None, raw=True)
        assert status == 200
        html = body.decode()
        assert 'rel="manifest"' in html
        assert "serviceWorker" in html
        assert 'id="reachbtn"' in html


class TestPushEndpoints:
    def test_vapid_endpoint_is_auth_gated(self, shell_and_sense):
        _, sense = shell_and_sense
        status, doc, _ = call(sense, "GET", "/api/push/vapid")
        assert status == 200
        assert doc["public_key"]
        assert doc["person"] == "christopher"
        status, _, _ = call(sense, "GET", "/api/push/vapid", token="wrong")
        assert status == 401

    def test_subscribe_lands_on_operator_record(self, shell_and_sense):
        shell, sense = shell_and_sense
        status, doc, _ = call(sense, "POST", "/api/push/subscribe",
                              {"subscription": SUB_A, "ua": "phone"})
        assert status == 200 and doc["ok"]
        assert doc["devices"] == 1
        subs = shell.entity.relationships.push_subscriptions("christopher")
        assert subs[0]["endpoint"] == SUB_A["endpoint"]
        assert subs[0]["ua"] == "phone"

    def test_subscribe_requires_auth_and_valid_body(self, shell_and_sense):
        _, sense = shell_and_sense
        status, _, _ = call(sense, "POST", "/api/push/subscribe",
                            {"subscription": SUB_A}, token=None)
        assert status == 401
        status, doc, _ = call(sense, "POST", "/api/push/subscribe",
                              {"subscription": {"endpoint": "x"}})
        assert status == 400

    def test_unsubscribe_round_trip(self, shell_and_sense):
        shell, sense = shell_and_sense
        call(sense, "POST", "/api/push/subscribe", {"subscription": SUB_A})
        status, doc, _ = call(sense, "POST", "/api/push/unsubscribe",
                              {"endpoint": SUB_A["endpoint"]})
        assert status == 200 and doc["removed"] is True
        assert doc["devices"] == 0
        assert shell.entity.relationships.push_subscriptions(
            "christopher") == []
        status, doc, _ = call(sense, "POST", "/api/push/unsubscribe",
                              {"endpoint": SUB_A["endpoint"]})
        assert doc["removed"] is False


# ── doctor: the reach checks ──────────────────────────────────────────

from anima.cli import main as cli_main          # noqa: E402
from anima.doctor import PASS, WARN, run_doctor  # noqa: E402

UP = lambda url: True  # noqa: E731


def _by_name(checks):
    return {c["name"]: c for c in checks}


@pytest.fixture()
def root(tmp_path):
    root = tmp_path / "ent"
    assert cli_main(["init", str(root)]) == 0
    return root


class TestDoctorReach:
    def test_init_root_has_vapid_pass_and_offline_pwa_pass(self, root):
        checks, code = run_doctor(str(root), probe=UP,
                                  fetch_status=lambda url: 200)
        named = _by_name(checks)
        assert named["vapid keypair"]["status"] == PASS
        # cold root: no pidlock → the doctor must NOT probe the port
        assert named["pwa shell"]["status"] == PASS

    def test_missing_vapid_is_warn_not_fail(self, root, tmp_path):
        import shutil
        shutil.rmtree(root / "identity" / "vapid")
        checks, _ = run_doctor(str(root), probe=UP,
                               fetch_status=lambda url: 200)
        assert _by_name(checks)["vapid keypair"]["status"] == WARN

    def test_loose_private_key_mode_is_warn(self, root):
        os.chmod(root / "identity" / "vapid" / "private_key", 0o644)
        checks, _ = run_doctor(str(root), probe=UP,
                               fetch_status=lambda url: 200)
        c = _by_name(checks)["vapid keypair"]
        assert c["status"] == WARN and "0600" in c["reason"]

    def test_live_root_probes_assets_via_injected_fetch(self, root):
        # a listener the TEST owns — never the real entity's port
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            (root / "senses" / "web.json").write_text(json.dumps(
                {"auth": "open", "bind": "127.0.0.1", "port": port}))
            (root / "runtime.pid").write_text(f"{os.getpid()}\n")
            seen = []

            def fetch(url):
                seen.append(url)
                return 200 if "manifest" in url else 404

            checks, _ = run_doctor(str(root), probe=UP, fetch_status=fetch)
            named = _by_name(checks)
            assert named["pwa manifest"]["status"] == PASS
            assert named["pwa service worker"]["status"] == WARN
            assert all(u.startswith(f"http://127.0.0.1:{port}")
                       for u in seen)
        finally:
            srv.close()
