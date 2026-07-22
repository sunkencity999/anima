"""Shared fakes for the Phase 5 runtime tests. Fully offline."""

from __future__ import annotations

import json
from types import SimpleNamespace

from anima.routing import Router, RoutingPolicy, TransportResult


def make_policy():
    return RoutingPolicy.from_dict({
        "tiers": {
            tier: {"candidates": [{
                "provider": "local", "model": "fake",
                "base_url": "http://fake.test/v1", "local": True,
            }]}
            for tier in ("reflex", "standard", "deep")
        }
    })


def tool_call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args)}}


def body(content="", tool_calls=None, finish_reason="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "x", "model": "fake",
            "choices": [{"message": msg, "finish_reason": finish_reason}]}


class SequenceTransport:
    """Pops scripted responses one call at a time. Each item is a body
    dict, an Exception to raise, or a callable(payload) -> body dict
    (for responses that must depend on the accumulated messages)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # [(candidate_id, payload), ...]

    def __call__(self, candidate, payload, timeout_s):
        self.calls.append((candidate.id, payload))
        if not self.script:
            raise AssertionError("transport script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(payload)
        return TransportResult(body=item)


def make_router(script, clock=lambda: 0.0):
    transport = SequenceTransport(script)
    router = Router(make_policy(), transport,
                    sleep=lambda s: None, clock=clock)
    return router, transport


class RecordingRouter:
    """Minimal Router stand-in: records (tier, messages, tools) and pops
    scripted RoutedResult-shaped namespaces."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []  # [(tier, messages, tools), ...]

    def complete(self, tier, messages, tools=None, **kw):
        self.calls.append((tier, [dict(m) for m in messages], tools))
        if not self.results:
            raise AssertionError("router script exhausted")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @staticmethod
    def result(content="", tool_calls=None):
        return SimpleNamespace(
            content=content, tool_calls=tool_calls, model_used="fake",
            provider="local", attempts=[], degraded=False,
            failover_events=[], raw_body=None)
