"""Router tests — chain walking, retries, failover, audit. Fully offline."""

import json

import pytest

from anima.routing import (
    Router,
    RoutingExhausted,
    RoutingPolicy,
    TransportError,
    TransportResult,
)


# ── helpers ───────────────────────────────────────────────────────────

def make_policy(n_candidates=3, **tier_kw):
    cands = []
    names = ["alpha", "beta", "gamma", "delta"][:n_candidates]
    for name in names:
        cands.append({
            "provider": f"prov-{name}",
            "model": f"model-{name}",
            "base_url": f"http://{name}.test/v1",
            "local": name in ("gamma", "delta"),
        })
    doc = {"tiers": {"standard": {"candidates": cands, **tier_kw}}}
    return RoutingPolicy.from_dict(doc)


def ok_body(content="A fine reply.", model="m", tool_calls=None,
            finish_reason="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "x", "model": model,
            "choices": [{"message": msg, "finish_reason": finish_reason}]}


class ScriptedTransport:
    """Feed per-candidate scripted outcomes. Each entry is a list consumed
    call by call: TransportResult, TransportError, or a body dict."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, candidate, payload, timeout_s):
        self.calls.append((candidate.id, payload))
        queue = self.script.get(candidate.model)
        if not queue:
            raise AssertionError(f"unexpected call to {candidate.id}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return TransportResult(body=item)
        return item


def make_router(policy, transport, **kw):
    sleeps = []
    router = Router(policy, transport, sleep=sleeps.append,
                    clock=lambda: 0.0, **kw)
    return router, sleeps


MSGS = [{"role": "user", "content": "hi"}]


# ── the historical bug, reproduced and fixed ──────────────────────────

class TestEmptyReplyBug:
    def test_empty_reply_from_A_continues_to_B(self):
        """THE bug (2026-07-19): empty reply from candidate A was classified
        success and the chain terminated. Here it MUST continue to B."""
        t = ScriptedTransport({
            "model-alpha": [ok_body(content="")],       # empty — NOT success
            "model-beta": [ok_body(content="real answer")],
        })
        router, _ = make_router(make_policy(), t)
        result = router.complete("standard", MSGS)
        assert result.content == "real answer"
        assert result.model_used == "model-beta"
        assert result.degraded is True
        assert len(result.failover_events) == 1
        assert result.failover_events[0]["reason"] == "empty_reply"
        # audit: alpha attempt recorded as contract failure
        outcomes = [(a.candidate, a.outcome, a.reason) for a in result.attempts]
        assert outcomes[0] == ("prov-alpha/model-alpha", "contract_failed",
                               "empty_reply")
        assert outcomes[1][1] == "ok"

    def test_whitespace_reply_also_fails_over(self):
        t = ScriptedTransport({
            "model-alpha": [ok_body(content="  \n ")],
            "model-beta": [ok_body(content="substance")],
        })
        router, _ = make_router(make_policy(), t)
        result = router.complete("standard", MSGS)
        assert result.model_used == "model-beta"

    def test_contract_failure_not_retried_on_same_candidate(self):
        """Empty reply → straight to next candidate, no same-model retry."""
        t = ScriptedTransport({
            "model-alpha": [ok_body(content="")],
            "model-beta": [ok_body(content="fine")],
        })
        router, _ = make_router(make_policy(), t)
        router.complete("standard", MSGS)
        alpha_calls = [c for c, _ in t.calls if "alpha" in c]
        assert len(alpha_calls) == 1


class TestAnthropicShaped400:
    def test_400_body_fails_over_not_retries(self):
        body = json.dumps({"type": "error", "error": {
            "type": "invalid_request_error", "message": "schema no good"}})
        t = ScriptedTransport({
            "model-alpha": [TransportError("400", status=400, body=body)],
            "model-beta": [ok_body(content="served by beta")],
        })
        router, sleeps = make_router(make_policy(), t)
        result = router.complete("standard", MSGS)
        assert result.model_used == "model-beta"
        assert sleeps == []  # no same-model retry backoffs
        assert result.attempts[0].reason == "invalid_request"
        assert result.attempts[0].decision == "failover_next"


class TestDeploymentNotFound:
    def test_deployment_not_found_is_never_success(self):
        body = json.dumps({"error": {
            "code": "DeploymentNotFound",
            "message": "The API deployment for this resource does not exist"}})
        t = ScriptedTransport({
            "model-alpha": [TransportError("404", status=404, body=body)],
            "model-beta": [ok_body(content="beta lives")],
        })
        router, _ = make_router(make_policy(), t)
        result = router.complete("standard", MSGS)
        assert result.model_used == "model-beta"
        assert result.degraded is True
        alpha = result.attempts[0]
        assert alpha.outcome == "transport_error"
        assert alpha.reason == "not_found"
        # the dead deployment must never appear as an ok attempt
        assert all(a.outcome != "ok" for a in result.attempts
                   if "alpha" in a.candidate)


class TestRetrySameBounded:
    def test_429_retries_then_fails_over(self):
        err = lambda: TransportError(
            "429", status=429,
            body={"error": {"type": "rate_limit_error", "message": "slow"}})
        t = ScriptedTransport({
            "model-alpha": [err(), err(), err(), err()],  # never recovers
            "model-beta": [ok_body(content="beta answer")],
        })
        router, sleeps = make_router(make_policy(max_retries_same=2), t)
        result = router.complete("standard", MSGS)
        # 1 initial + 2 retries = 3 alpha calls, then failover
        assert len([c for c, _ in t.calls if "alpha" in c]) == 3
        assert len(sleeps) == 2  # backoff before each retry
        assert sleeps[0] < sleeps[1]  # exponential
        assert result.model_used == "model-beta"

    def test_429_recovers_on_retry(self):
        err = TransportError(
            "429", status=429,
            body={"error": {"type": "rate_limit_error", "message": "slow"}})
        t = ScriptedTransport({
            "model-alpha": [err, ok_body(content="recovered")],
        })
        router, sleeps = make_router(make_policy(1), t)
        result = router.complete("standard", MSGS)
        assert result.content == "recovered"
        assert result.model_used == "model-alpha"
        # retried same model, so no failover events — but attempts show both
        assert result.failover_events == []
        assert len(result.attempts) == 2
        # degraded stays False: first-choice model ultimately served
        assert result.degraded is False

    def test_auth_error_gets_exactly_one_retry(self):
        err = lambda: TransportError(
            "401", status=401,
            body={"error": {"type": "authentication_error", "message": "bad key"}})
        t = ScriptedTransport({
            "model-alpha": [err(), err(), err()],
            "model-beta": [ok_body(content="beta")],
        })
        # tier budget is generous (5) but auth clamps to exactly 1 retry
        router, _ = make_router(make_policy(max_retries_same=5), t)
        result = router.complete("standard", MSGS)
        assert len([c for c, _ in t.calls if "alpha" in c]) == 2
        assert result.model_used == "model-beta"


class TestExhaustion:
    def test_all_candidates_exhausted_raises_with_audit(self):
        t = ScriptedTransport({
            "model-alpha": [ok_body(content="")],
            "model-beta": [TransportError("500", status=500),
                           TransportError("500", status=500),
                           TransportError("500", status=500)],
            "model-gamma": [ok_body(content="", finish_reason="content_filter")],
        })
        router, _ = make_router(make_policy(max_retries_same=2), t)
        with pytest.raises(RoutingExhausted) as ei:
            router.complete("standard", MSGS)
        exc = ei.value
        assert exc.tier == "standard"
        # full audit present: 1 alpha + 3 beta + 1 gamma
        assert len(exc.attempts) == 5
        reasons = {a.candidate: a.reason for a in exc.attempts}
        assert reasons["prov-alpha/model-alpha"] == "empty_reply"
        assert "server_error" in [a.reason for a in exc.attempts]
        assert str(exc)  # message is informative
        assert "standard" in str(exc)


class TestContractInRouter:
    def test_error_json_in_200_fails_over(self):
        t = ScriptedTransport({
            "model-alpha": [{"type": "error", "error": {
                "type": "overloaded_error", "message": "Overloaded"}}],
            "model-beta": [ok_body(content="beta ok")],
        })
        router, _ = make_router(make_policy(), t)
        result = router.complete("standard", MSGS)
        assert result.model_used == "model-beta"
        assert result.attempts[0].reason == "error_payload"

    def test_malformed_tool_call_fails_over(self):
        bad_tc = [{"function": {"name": "run", "arguments": "{broken"}}]
        good_tc = [{"function": {"name": "run",
                                 "arguments": json.dumps({"cmd": "ls"})}}]
        t = ScriptedTransport({
            "model-alpha": [ok_body(content="", tool_calls=bad_tc,
                                    finish_reason="tool_calls")],
            "model-beta": [ok_body(content="", tool_calls=good_tc,
                                   finish_reason="tool_calls")],
        })
        router, _ = make_router(make_policy(), t)
        tools = [{"type": "function", "function": {"name": "run"}}]
        result = router.complete("standard", MSGS, tools=tools)
        assert result.model_used == "model-beta"
        assert result.tool_calls == good_tc
        assert result.attempts[0].reason == "malformed_tool_call"

    def test_content_filter_finish_fails_over(self):
        t = ScriptedTransport({
            "model-alpha": [ok_body(content="partial",
                                    finish_reason="content_filter")],
            "model-beta": [ok_body(content="complete")],
        })
        router, _ = make_router(make_policy(), t)
        result = router.complete("standard", MSGS)
        assert result.model_used == "model-beta"
        assert result.attempts[0].reason == "content_filter"


class TestDegradedFlag:
    def test_first_choice_not_degraded(self):
        t = ScriptedTransport({"model-alpha": [ok_body(content="primary")]})
        router, _ = make_router(make_policy(), t)
        result = router.complete("standard", MSGS)
        assert result.degraded is False
        assert result.failover_events == []

    def test_second_choice_sets_degraded(self):
        t = ScriptedTransport({
            "model-alpha": [TransportError("boom", status=500),
                            TransportError("boom", status=500),
                            TransportError("boom", status=500)],
            "model-beta": [ok_body(content="backup")],
        })
        router, _ = make_router(make_policy(max_retries_same=2), t)
        result = router.complete("standard", MSGS)
        assert result.degraded is True
        assert result.failover_events[0]["from"] == "prov-alpha/model-alpha"
        assert result.failover_events[0]["to"] == "prov-beta/model-beta"


class TestLedgerIntegration:
    def test_failover_events_logged_to_ledger(self, tmp_path):
        """Optional Phase-2 ledger wiring: failovers become ledger rows."""
        from anima.wake.ledger import Ledger

        t = ScriptedTransport({
            "model-alpha": [ok_body(content="")],
            "model-beta": [ok_body(content="served")],
        })
        with Ledger(str(tmp_path / "entity")) as ledger:
            router, _ = make_router(make_policy(), t, ledger=ledger,
                                    wake_id="wake-42")
            router.complete("standard", MSGS)
            rows = ledger.for_wake("wake-42")
        kinds = [r["kind"] for r in rows]
        assert "failover" in kinds
        assert "routing_degraded" in kinds
        fo = next(r for r in rows if r["kind"] == "failover")
        detail = json.loads(fo["detail"])
        assert detail["reason"] == "empty_reply"
        assert fo["outcome"] == "error"

    def test_router_works_without_ledger(self):
        t = ScriptedTransport({"model-alpha": [ok_body(content="fine")]})
        router, _ = make_router(make_policy(), t)  # no ledger at all
        assert router.complete("standard", MSGS).content == "fine"


class TestStandalone:
    def test_routing_importable_without_memory_or_wake(self):
        """The routing package must not import anima.memory / anima.wake."""
        import subprocess, sys
        code = (
            "import sys\n"
            "import anima.routing\n"
            "bad = [m for m in sys.modules"
            " if m.startswith(('anima.memory', 'anima.wake'))]\n"
            "assert not bad, f'routing dragged in: {bad}'\n"
            "print('standalone-ok')\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            cwd=str(__import__('pathlib').Path(__file__).resolve().parents[1]),
        )
        assert out.returncode == 0, out.stderr
        assert "standalone-ok" in out.stdout
