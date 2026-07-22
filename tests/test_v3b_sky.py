"""Observatory v3b shared sky: the aggregator polls real peer
Observatories (two live WebSense instances on ephemeral loopback
ports), serves the sky page + /api/sky, keeps peer tokens server-side,
degrades gracefully on unreachable peers, and derives migration edges
from lineage."""

import json
import os
import urllib.error
import urllib.request

import pytest

from anima.runtime import RuntimeShell
from anima.runtime.sky import SkyAggregator
from anima.runtime.senses.web_sense import WebSense

T0 = 1_784_000_000.0
SKY_TOKEN = "sky-sekrit"
PEER_TOKEN_A = "peer-a-token-3f9"
PEER_TOKEN_B = "peer-b-token-7c2"


@pytest.fixture()
def two_peers(tmp_path):
    """Two live entities (luna, nova) each serving an Observatory."""
    peers = []
    for name, token in (("luna", PEER_TOKEN_A), ("nova", PEER_TOKEN_B)):
        shell = RuntimeShell(str(tmp_path / name), clock=lambda: T0)
        sense = WebSense({"port": 0, "token": token,
                          "bind": "127.0.0.1",
                          "operator_person": "christopher"})
        shell.add_sense("web", sense)
        shell.start()
        peers.append((name, shell, sense, token))
    yield peers
    for _, shell, _, _ in peers:
        shell.shutdown()


def sky_config(peers, extra_peers=(), **over):
    cfg = {"port": 0, "bind": "127.0.0.1", "token": SKY_TOKEN,
           "poll_s": 60, "timeout_s": 2,
           "peers": [{"url": f"http://127.0.0.1:{sense.port}",
                      "token": token}
                     for _, _, sense, token in peers]
                    + list(extra_peers)}
    cfg.update(over)
    return cfg


@pytest.fixture()
def sky(two_peers):
    agg = SkyAggregator(sky_config(two_peers))
    agg.refresh()
    agg.start()
    yield agg
    agg.stop()


def call(port, path, *, token=SKY_TOKEN, raw=False):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
    if raw:
        return status, body
    return status, json.loads(body.decode())


class TestSkyConfig:
    def test_token_mode_requires_token(self, two_peers):
        cfg = sky_config(two_peers)
        cfg["auth"] = "token"
        cfg["token"] = ""
        with pytest.raises(ValueError, match="token"):
            SkyAggregator(cfg)

    def test_bad_auth_value_refused(self, two_peers):
        cfg = sky_config(two_peers, auth="banana")
        with pytest.raises(ValueError, match="auth"):
            SkyAggregator(cfg)

    def test_requires_peers(self):
        with pytest.raises(ValueError, match="peer"):
            SkyAggregator({"token": "t", "peers": []})

    def test_rejects_non_http_peer_url(self):
        with pytest.raises(ValueError, match="http"):
            SkyAggregator({"token": "t", "peers": [
                {"url": "file:///etc/passwd", "token": "x"}]})


class TestSkyAuth:
    def test_api_rejects_bad_token(self, sky):
        status, doc = call(sky.port, "/api/sky", token="wrong")
        assert status == 401 and doc["error"] == "unauthorized"

    def test_page_locked_without_token(self, sky):
        status, body = call(sky.port, "/", token=None, raw=True)
        assert status == 401 and b"DOME IS CLOSED" in body

    def test_query_token_serves_page_and_cookie(self, sky):
        req = urllib.request.Request(
            f"http://127.0.0.1:{sky.port}/?token={SKY_TOKEN}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            assert "anima_sky=" in (resp.headers.get("Set-Cookie") or "")
            assert b"shared sky" in resp.read()

    def test_unknown_endpoint_404(self, sky):
        status, doc = call(sky.port, "/api/nope")
        assert status == 404


class TestSkyOpenAuth:
    """Home-mode: auth="open" serves the sky to anyone on the wire;
    legacy configs (token, no auth key) keep token behavior."""

    @pytest.fixture()
    def open_sky(self, two_peers):
        cfg = sky_config(two_peers, auth="open")
        del cfg["token"]
        agg = SkyAggregator(cfg)
        agg.refresh()
        agg.start()
        yield agg
        agg.stop()

    def test_page_serves_without_any_auth(self, open_sky):
        status, body = call(open_sky.port, "/", token=None, raw=True)
        assert status == 200 and b"shared sky" in body

    def test_api_serves_without_any_auth(self, open_sky):
        status, doc = call(open_sky.port, "/api/sky", token=None)
        assert status == 200
        assert sorted(p["name"] for p in doc["peers"]) == ["luna",
                                                           "nova"]

    def test_open_mode_sets_no_cookie(self, open_sky):
        req = urllib.request.Request(
            f"http://127.0.0.1:{open_sky.port}/")
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.headers.get("Set-Cookie") is None

    def test_legacy_config_with_token_stays_gated(self, sky):
        # the `sky` fixture has a token and no "auth" key — inference
        # must keep it token-gated
        assert sky.auth == "token"
        status, _ = call(sky.port, "/api/sky", token=None)
        assert status == 401

    def test_explicit_open_wins_over_present_token(self, two_peers):
        agg = SkyAggregator(sky_config(two_peers, auth="open"))
        assert agg.auth == "open"

    def test_open_peer_polled_without_authorization(self, tmp_path):
        """A token-less peer entry polls an open-mode Observatory
        successfully (no Authorization header sent)."""
        from anima.runtime import RuntimeShell
        shell = RuntimeShell(str(tmp_path / "soleil"),
                             clock=lambda: T0)
        sense = WebSense({"port": 0, "auth": "open",
                          "bind": "127.0.0.1",
                          "operator_person": "christopher"})
        shell.add_sense("web", sense)
        shell.start()
        try:
            agg = SkyAggregator({
                "port": 0, "bind": "127.0.0.1", "auth": "open",
                "timeout_s": 2,
                "peers": [{"url":
                           f"http://127.0.0.1:{sense.port}"}]})
            snap = agg.refresh()
            assert snap["peers"][0]["reachable"] is True
            assert snap["peers"][0]["name"] == "soleil"
        finally:
            shell.shutdown()


class TestSkyAggregation:
    def test_both_peers_reported_live(self, sky):
        status, doc = call(sky.port, "/api/sky")
        assert status == 200
        names = sorted(p["name"] for p in doc["peers"])
        assert names == ["luna", "nova"]
        for p in doc["peers"]:
            assert p["reachable"] is True
            assert p["error"] is None
            assert p["stats"]["episodes"] is not None
            assert isinstance(p["drives"], list)
            assert p["lineage"], "lineage should carry the init entry"
            assert p["lineage"][0]["kind"] == "init"

    def test_peer_tokens_never_leave_the_server(self, sky):
        _, body = call(sky.port, "/api/sky", raw=True)
        assert PEER_TOKEN_A.encode() not in body
        assert PEER_TOKEN_B.encode() not in body
        req = urllib.request.Request(
            f"http://127.0.0.1:{sky.port}/?token={SKY_TOKEN}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read()
        assert PEER_TOKEN_A.encode() not in page
        assert PEER_TOKEN_B.encode() not in page

    def test_peer_url_is_shipped_for_the_card_link(self, sky):
        _, doc = call(sky.port, "/api/sky")
        for p in doc["peers"]:
            assert p["url"].startswith("http://127.0.0.1:")

    def test_unreachable_peer_degrades_gracefully(self, two_peers):
        agg = SkyAggregator(sky_config(
            two_peers,
            extra_peers=[{"name": "ghost",
                          "url": "http://127.0.0.1:9",  # discard port
                          "token": "irrelevant"}]))
        agg.refresh()
        agg.start()
        try:
            status, doc = call(agg.port, "/api/sky")
            assert status == 200
            ghost = next(p for p in doc["peers"] if p["name"] == "ghost")
            assert ghost["reachable"] is False
            assert ghost["error"]
            live = [p for p in doc["peers"] if p["reachable"]]
            assert len(live) == 2, "live peers unaffected by the ghost"
        finally:
            agg.stop()

    def test_wrong_peer_token_reads_as_unreachable(self, two_peers):
        (name, _, sense, _) = two_peers[0]
        agg = SkyAggregator({
            "port": 0, "bind": "127.0.0.1", "token": SKY_TOKEN,
            "timeout_s": 2,
            "peers": [{"name": "locked",
                       "url": f"http://127.0.0.1:{sense.port}",
                       "token": "not-the-token"}]})
        snap = agg.refresh()
        assert snap["peers"][0]["reachable"] is False

    def test_last_expression_resanitized_at_the_sky(self, two_peers,
                                                    tmp_path):
        name, shell, sense, token = two_peers[0]
        # plant a stored expression that (hypothetically) dodged the
        # peer's own sanitizer — the sky must scrub it again
        shell.entity.store.db.execute(
            "INSERT INTO expressions (ts, title, kind, body)"
            " VALUES (?,?,?,?)",
            (T0, "sneaky", "html",
             '<div onclick="alert(1)">hi<script>evil()</script></div>'))
        shell.entity.store.db.commit()
        agg = SkyAggregator(sky_config(two_peers))
        snap = agg.refresh()
        luna = next(p for p in snap["peers"] if p["name"] == "luna")
        assert luna["expression"]["body"] == "<div>hi</div>"


class TestMigrationEdges:
    def test_edge_between_clusters(self, two_peers, tmp_path):
        # a legitimate anima-sync-shaped migration record on luna,
        # whose dest basename is the other cluster's name
        luna_root = str(tmp_path / "luna")
        with open(os.path.join(luna_root, "identity", "lineage.log"),
                  "a", encoding="utf-8") as f:
            f.write("2026-07-22T00:00:00Z | migration | migrated from "
                    f"testhost:{tmp_path}/luna -> {tmp_path}/nova "
                    "(anima sync)\n")
        agg = SkyAggregator(sky_config(two_peers))
        snap = agg.refresh()
        assert snap["edges"] == [{
            "from": "luna", "to": "nova",
            "ts": "2026-07-22T00:00:00Z",
            "detail": snap["edges"][0]["detail"]}]
        assert "anima sync" in snap["edges"][0]["detail"]

    def test_duplicate_records_on_both_forks_dedup(self, two_peers,
                                                   tmp_path):
        line = ("2026-07-22T00:00:00Z | migration | migrated from "
                f"testhost:{tmp_path}/luna -> {tmp_path}/nova "
                "(anima sync)\n")
        for name in ("luna", "nova"):     # both forks carry the record
            with open(os.path.join(str(tmp_path / name), "identity",
                                   "lineage.log"), "a",
                      encoding="utf-8") as f:
                f.write(line)
        agg = SkyAggregator(sky_config(two_peers))
        snap = agg.refresh()
        assert len(snap["edges"]) == 1

    def test_migration_to_unknown_dest_draws_no_edge(self, two_peers,
                                                     tmp_path):
        with open(os.path.join(str(tmp_path / "luna"), "identity",
                               "lineage.log"), "a",
                  encoding="utf-8") as f:
            f.write("2026-07-22T00:00:00Z | migration | migrated from "
                    f"testhost:{tmp_path}/luna -> /mnt/elsewhere/zed "
                    "(anima sync)\n")
        agg = SkyAggregator(sky_config(two_peers))
        snap = agg.refresh()
        assert snap["edges"] == []
