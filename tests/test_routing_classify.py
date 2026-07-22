"""Error classifier tests — the real-world provider error shapes."""

import json

from anima.routing.classify import Decision, classify_error


class TestAnthropicShapes:
    def test_anthropic_400_body_without_status_is_failover(self):
        """THE scar (2026-07-02): Anthropic-shaped 400 body with NO leading
        HTTP status context must classify failover_next, not retry_same."""
        body = json.dumps({
            "type": "error",
            "error": {"type": "invalid_request_error",
                      "message": "messages: text content blocks must be non-empty"},
        })
        c = classify_error(None, body)
        assert c.decision is Decision.FAILOVER_NEXT
        assert c.reason == "invalid_request"

    def test_anthropic_400_dict_body(self):
        body = {"type": "error",
                "error": {"type": "invalid_request_error", "message": "bad"}}
        c = classify_error(400, body)
        assert c.decision is Decision.FAILOVER_NEXT

    def test_anthropic_overloaded_is_retry(self):
        body = {"type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"}}
        c = classify_error(529, body)
        assert c.decision is Decision.RETRY_SAME

    def test_body_with_log_prefix_junk_still_parses(self):
        body = ('provider said: {"type":"error","error":'
                '{"type":"invalid_request_error","message":"nope"}}')
        c = classify_error(None, body)
        assert c.decision is Decision.FAILOVER_NEXT


class TestAzureShapes:
    def test_deployment_not_found_is_failover_never_success(self):
        """THE scar (2026-07-09): DeploymentNotFound was marked
        candidate_succeeded. It must be failover_next with zero retries."""
        body = {"error": {"code": "DeploymentNotFound",
                          "message": "The API deployment for this resource does not exist."}}
        c = classify_error(404, body)
        assert c.decision is Decision.FAILOVER_NEXT
        assert c.reason == "not_found"
        assert c.max_same_retries == 0

    def test_deployment_not_found_without_status(self):
        body = json.dumps({"error": {"code": "DeploymentNotFound",
                                     "message": "does not exist"}})
        c = classify_error(None, body)
        assert c.decision is Decision.FAILOVER_NEXT
        assert c.reason == "not_found"


class TestOpenAIShapes:
    def test_insufficient_quota_is_failover_not_abort(self):
        body = {"error": {"type": "insufficient_quota",
                          "message": "You exceeded your current quota"}}
        c = classify_error(429, body)
        # billing on ONE provider fails over; the next provider may be fine
        assert c.decision is Decision.FAILOVER_NEXT
        assert c.reason == "billing"

    def test_billing_hard_limit(self):
        body = {"error": {"code": "billing_hard_limit_reached",
                          "message": "Billing hard limit has been reached"}}
        c = classify_error(400, body)
        assert c.decision is Decision.FAILOVER_NEXT
        assert c.reason == "billing"

    def test_plain_429_is_retry(self):
        body = {"error": {"type": "rate_limit_error", "message": "slow down"}}
        c = classify_error(429, body)
        assert c.decision is Decision.RETRY_SAME
        assert c.reason == "rate_limit"

    def test_model_not_found(self):
        body = {"error": {"code": "model_not_found",
                          "message": "The model does not exist"}}
        c = classify_error(404, body)
        assert c.decision is Decision.FAILOVER_NEXT


class TestStatusOnly:
    def test_429_no_body(self):
        c = classify_error(429)
        assert c.decision is Decision.RETRY_SAME

    def test_500_502_503_retry(self):
        for s in (500, 502, 503, 504):
            assert classify_error(s).decision is Decision.RETRY_SAME

    def test_400_failover(self):
        assert classify_error(400).decision is Decision.FAILOVER_NEXT

    def test_404_failover(self):
        assert classify_error(404).decision is Decision.FAILOVER_NEXT

    def test_auth_gets_one_retry_budget(self):
        """Auth errors: retry once (token refresh window), then failover —
        never unbounded retries on a dead key."""
        c = classify_error(401)
        assert c.decision is Decision.RETRY_SAME
        assert c.max_same_retries == 1
        assert classify_error(403).reason == "auth"

    def test_402_billing(self):
        assert classify_error(402).reason == "billing"


class TestUnknown:
    def test_unknown_never_succeeds_and_never_retries_forever(self):
        """Unknown errors default to FAILOVER — the safe direction. The
        production bug mapped unknown → success; encode the opposite."""
        c = classify_error(None, "something completely novel exploded")
        assert c.decision is Decision.FAILOVER_NEXT
        assert c.reason == "unknown"

    def test_no_status_no_body(self):
        c = classify_error(None, None)
        assert c.decision is Decision.FAILOVER_NEXT
