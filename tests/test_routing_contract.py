"""Contract verifier tests — the invariants that make the bug classes impossible."""

import json

import pytest

from anima.routing.contract import Reason, verify_response


class TestEmptyReplyInvariant:
    """An empty reply is ALWAYS a contract failure. Not configurable."""

    def test_empty_string_fails(self):
        r = verify_response("")
        assert not r.ok
        assert r.reason is Reason.EMPTY_REPLY

    def test_none_fails(self):
        r = verify_response(None)
        assert not r.ok
        assert r.reason is Reason.EMPTY_REPLY

    def test_whitespace_only_fails(self):
        r = verify_response("   \n\t  ")
        assert not r.ok
        assert r.reason is Reason.EMPTY_REPLY

    def test_min_content_chars_zero_cannot_permit_empty(self):
        """The scar: config must never be able to bless an empty reply."""
        r = verify_response("", min_content_chars=0)
        assert not r.ok
        assert r.reason is Reason.EMPTY_REPLY

    def test_negative_min_chars_cannot_permit_empty(self):
        r = verify_response("", min_content_chars=-5)
        assert not r.ok

    def test_valid_tool_calls_without_content_pass(self):
        """Tool-call-only turns are legitimate (not 'empty')."""
        tc = [{"function": {"name": "get_weather",
                            "arguments": json.dumps({"city": "SF"})}}]
        r = verify_response("", tool_calls=tc, finish_reason="tool_calls")
        assert r.ok

    def test_nonempty_content_passes(self):
        r = verify_response("Hello there.", finish_reason="stop")
        assert r.ok
        assert r.reason is Reason.OK


class TestErrorPayloadDisguisedAs200:
    def test_anthropic_error_body_fails(self):
        body = {"type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"}}
        r = verify_response("anything", raw_body=body)
        assert not r.ok
        assert r.reason is Reason.ERROR_PAYLOAD

    def test_openai_error_body_fails(self):
        body = {"error": {"code": "DeploymentNotFound",
                          "message": "The API deployment does not exist"}}
        r = verify_response("anything", raw_body=body)
        assert not r.ok
        assert r.reason is Reason.ERROR_PAYLOAD

    def test_error_json_in_content_fails(self):
        content = json.dumps(
            {"error": {"type": "server_error", "message": "boom"}})
        r = verify_response(content)
        assert not r.ok
        assert r.reason is Reason.ERROR_PAYLOAD

    def test_normal_json_content_passes(self):
        r = verify_response(json.dumps({"answer": 42}))
        assert r.ok

    def test_clean_body_passes(self):
        body = {"choices": [{"message": {"content": "hi"}}]}
        r = verify_response("hi", raw_body=body)
        assert r.ok


class TestToolCalls:
    def test_malformed_arguments_fail(self):
        tc = [{"function": {"name": "f", "arguments": "{not json"}}]
        r = verify_response("", tool_calls=tc)
        assert not r.ok
        assert r.reason is Reason.MALFORMED_TOOL_CALL

    def test_missing_name_fails(self):
        tc = [{"function": {"arguments": "{}"}}]
        r = verify_response("", tool_calls=tc)
        assert not r.ok
        assert r.reason is Reason.MALFORMED_TOOL_CALL

    def test_missing_arguments_fails(self):
        tc = [{"function": {"name": "f"}}]
        r = verify_response("", tool_calls=tc)
        assert not r.ok
        assert r.reason is Reason.MALFORMED_TOOL_CALL

    def test_dict_arguments_pass(self):
        tc = [{"function": {"name": "f", "arguments": {"x": 1}}}]
        r = verify_response("", tool_calls=tc)
        assert r.ok

    def test_non_object_json_arguments_fail(self):
        tc = [{"function": {"name": "f", "arguments": "[1,2,3]"}}]
        r = verify_response("", tool_calls=tc)
        assert not r.ok
        assert r.reason is Reason.MALFORMED_TOOL_CALL

    def test_second_bad_call_caught(self):
        tc = [
            {"function": {"name": "good", "arguments": "{}"}},
            {"function": {"name": "bad", "arguments": "{{{"}},
        ]
        r = verify_response("", tool_calls=tc)
        assert not r.ok


class TestFinishReasons:
    def test_content_filter_fails(self):
        r = verify_response("partial text", finish_reason="content_filter")
        assert not r.ok
        assert r.reason is Reason.CONTENT_FILTER

    def test_error_finish_fails(self):
        r = verify_response("text", finish_reason="error")
        assert not r.ok
        assert r.reason is Reason.ERROR_PAYLOAD

    def test_stop_and_length_pass(self):
        assert verify_response("text", finish_reason="stop").ok
        assert verify_response("text", finish_reason="length").ok


class TestOptionalChecks:
    def test_min_content_chars_enforced(self):
        r = verify_response("short", min_content_chars=100)
        assert not r.ok
        assert r.reason is Reason.TOO_SHORT

    def test_refusal_heuristic_opt_in(self):
        text = "I'm sorry, but I can't help with that request."
        assert verify_response(text).ok  # off by default
        r = verify_response(text, check_refusal=True)
        assert not r.ok
        assert r.reason is Reason.REFUSAL_SHAPED

    def test_refusal_heuristic_does_not_flag_normal_text(self):
        r = verify_response("Sure — here's the plan.", check_refusal=True)
        assert r.ok
