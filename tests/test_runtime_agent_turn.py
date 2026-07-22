"""The act phase, end to end, offline: fake Router transport under the
REAL Phase 3 Router + contract, dispatched through the REAL Phase 2
scheduler + settle guard."""

import json

import pytest

from anima.entity import EntityRoot
from anima.relationships import AccessContext
from anima.routing import TransportError
from anima.runtime import (
    attach_agent_turn,
    default_registry,
    parse_settle_block,
    run_agent_turn,
    tier_for_wake,
)
from anima.wake.sources import Wake

from runtime_helpers import (
    RecordingRouter,
    body,
    make_router,
    tool_call,
)

T0 = 1_784_000_000.0


@pytest.fixture()
def entity(tmp_path):
    e = EntityRoot(str(tmp_path / "entity"), clock=lambda: T0)
    yield e
    e.close()


def run_message(entity, router, text="hello", sender="christopher",
                registry=None, context=None, budget=None):
    """Inject a message wake and dispatch through scheduler + guard."""
    registry = registry or default_registry()
    attach_agent_turn(entity, registry, router=router)
    ctx = context or AccessContext.direct(sender)
    wake = entity.messages.inject(sender, text, channel=ctx.channel or "chat",
                                  ts=T0)
    wake.payload["access_context"] = ctx.to_dict()
    if budget:
        wake.budget.update(budget)
    results = entity.scheduler.run_pending(now=T0)
    assert len(results) == 1
    return results[0]


SETTLE_FINAL = ("Done.\n\n```settle\n"
                + json.dumps({"summary": "remembered the fact",
                              "learnings": ["settle blocks work"]})
                + "\n```")


class TestAgentTurn:
    def test_tool_call_then_final(self, entity):
        router, transport = make_router([
            body(tool_calls=[tool_call(
                "remember", {"text": "christopher likes teal"})]),
            body(content=SETTLE_FINAL),
        ])
        result = run_message(entity, router)
        assert result["ok"] is True
        report = result["report"]
        assert report["actions_used"] == 1
        assert any(e["summary"] == "tool remember executed"
                   for e in report["events"])
        # tool result was fed back to the model on the second call
        _, payload2 = transport.calls[1]
        tool_msgs = [m for m in payload2["messages"] if m["role"] == "tool"]
        assert tool_msgs and '"ok": true' in tool_msgs[0]["content"]
        # the remembered learning + the settle-block learning both settled
        eps = entity.store.recent_episodes(20)
        summaries = " | ".join(e["summary"] for e in eps)
        assert "christopher likes teal" in summaries
        assert "settle blocks work" in summaries

    def test_settle_block_parsed_from_final(self, entity):
        router, _ = make_router([body(content=SETTLE_FINAL)])
        report = run_message(entity, router)["report"]
        assert any(e.get("summary") == "remembered the fact"
                   for e in report["events"] if isinstance(e, dict))
        assert "settle blocks work" in [
            l["detail"] if isinstance(l, dict) else l
            for l in report["learnings"]]

    def test_no_settle_block_synthesizes_from_trail(self, entity):
        router, _ = make_router([body(content="Just a plain answer.")])
        report = run_message(entity, router)["report"]
        assert any("wake handled" in e["summary"]
                   for e in report["events"] if isinstance(e, dict))

    def test_budget_exhaustion_truthful_report(self, entity):
        calls = [tool_call("reply", {"text": f"r{i}"}, f"c{i}")
                 for i in range(3)]
        router, _ = make_router([
            body(tool_calls=[calls[0]]),
            body(tool_calls=[calls[1]]),   # second execution exceeds budget
            body(tool_calls=[calls[2]]),
        ])
        result = run_message(entity, router,
                             budget={"max_actions": 1})
        report = result["report"]
        assert report["budget_exhausted"] is True
        assert report["actions_used"] == 1
        assert any("budget exhausted" in e["summary"]
                   for e in report["events"] if isinstance(e, dict))
        # and it still settled into memory
        assert result["receipt"]["episode_ids"]

    def test_risk_cap_filters_offered_schemas_and_blocks_execution(
            self, entity):
        """Both walls: a low-cap wake never sees http_get in its schema
        list, AND a hallucinated call to it is denied at execution."""
        router, transport = make_router([
            body(tool_calls=[tool_call("http_get",
                                       {"url": "http://x.test"})]),
            body(content="ok done"),
        ])
        result = run_message(entity, router,
                             budget={"risk_cap": "low"})
        # layer 1: offered schemas
        _, payload1 = transport.calls[0]
        offered = {t["function"]["name"] for t in payload1["tools"]}
        assert "http_get" not in offered
        # layer 2: execution denied, told to the model truthfully
        _, payload2 = transport.calls[1]
        tool_msgs = [m for m in payload2["messages"] if m["role"] == "tool"]
        assert "denied" in tool_msgs[0]["content"]
        # denial spent no budget
        assert result["report"]["actions_used"] == 0

    def test_recall_tool_respects_wake_access_context(self, entity):
        """Private row invisible inside a group-context wake turn."""
        entity.store.add_episode(
            summary="antonia's secret word is moonfern", kind="event",
            ts=T0, scope="private", owner="antonia")

        def run_recall(context):
            router, transport = make_router([
                body(tool_calls=[tool_call("recall",
                                           {"query": "secret word"})]),
                body(content="done"),
            ])
            run_message(entity, router, sender="antonia", context=context)
            _, payload2 = transport.calls[1]
            return [m for m in payload2["messages"]
                    if m["role"] == "tool"][0]["content"]

        group_result = run_recall(AccessContext.group(["antonia", "bob"]))
        assert "moonfern" not in group_result
        direct_result = run_recall(AccessContext.direct("antonia"))
        assert "moonfern" in direct_result

    def test_orient_pack_itself_is_acl_walled(self, entity):
        """Phase 5 hardening: the ORIENT PACK in the system prompt is
        built under the wake's AccessContext too — a group wake's
        prompt cannot leak private rows before the model even acts."""
        entity.store.add_episode(
            summary="antonia's secret word is moonfern", kind="event",
            ts=T0, scope="private", owner="antonia")
        router, transport = make_router([body(content="done")])
        run_message(entity, router, text="what is the secret word?",
                    sender="antonia",
                    context=AccessContext.group(["antonia", "bob"]))
        system = transport.calls[0][1]["messages"][0]["content"]
        assert "moonfern" not in system

    def test_tool_crash_turn_still_settles(self, entity):
        def boom(url, timeout_s=20.0):
            raise RuntimeError("kaboom")
        registry = default_registry(http_fetch=boom)
        router, transport = make_router([
            body(tool_calls=[tool_call("http_get",
                                       {"url": "http://x.test"})]),
            body(content="recovered"),
        ])
        result = run_message(entity, router, registry=registry)
        assert result["ok"] is True  # the TURN survived the tool crash
        assert any("FAILED" in e["summary"] and "kaboom" in e["summary"]
                   for e in result["report"]["events"]
                   if isinstance(e, dict))
        assert result["receipt"]["episode_ids"]

    def test_router_exhaustion_settles_failure_episode(self, entity):
        """Even a total routing collapse cannot skip settlement."""
        router, _ = make_router([
            TransportError("down", status=None, body="conn refused"),
        ])
        result = run_message(entity, router)
        assert result["ok"] is False
        assert "RoutingExhausted" in result["error"]
        eps = entity.store.recent_episodes(5)
        assert any("FAILED" in e["summary"] for e in eps)

    def test_replies_carried_in_report(self, entity):
        router, _ = make_router([
            body(tool_calls=[tool_call("reply", {"text": "hi back"})]),
            body(content="done"),
        ])
        report = run_message(entity, router)["report"]
        assert report["replies"] == ["hi back"]

    def test_every_tool_execution_is_a_ledger_row(self, entity):
        router, _ = make_router([
            body(tool_calls=[tool_call("reply", {"text": "a"})]),
            body(content="done"),
        ])
        result = run_message(entity, router)
        rows = entity.ledger.for_wake(result["wake"].wake_id)
        kinds = [r["kind"] for r in rows]
        assert "tool_call" in kinds and "model_call" in kinds


class TestTierSelection:
    def make_wake(self, source, payload=None):
        return Wake(wake_id="w", source=source, reason="r",
                    payload=payload or {})

    def test_wake_derived_defaults(self):
        assert tier_for_wake(self.make_wake("message")) == "standard"
        assert tier_for_wake(self.make_wake("drive")) == "reflex"
        assert tier_for_wake(self.make_wake("sense")) == "standard"
        assert tier_for_wake(self.make_wake("timer")) == "reflex"

    def test_payload_override_wins(self):
        wake = self.make_wake("drive", {"tier": "deep"})
        assert tier_for_wake(wake) == "deep"

    def test_custom_tier_map(self):
        wake = self.make_wake("message")
        assert tier_for_wake(wake, {"message": "deep"}) == "deep"

    def test_tier_reaches_router(self, entity):
        router = RecordingRouter([RecordingRouter.result("done")])
        registry = default_registry()
        wake = Wake(wake_id="w-timer", source="timer",
                    reason="scheduled check",
                    budget={"max_actions": 4, "risk_cap": "low"})
        run_agent_turn(entity, wake, registry, router=router, now=T0)
        assert router.calls[0][0] == "reflex"

    def test_self_initiated_wake_runs_as_system_context(self, entity):
        """A drive wake recalls with the entity's own full-visibility
        context — the organism knows its own mind."""
        entity.store.add_episode(
            summary="private note: moonfern", kind="event", ts=T0,
            scope="private", owner="antonia")
        router = RecordingRouter([
            RecordingRouter.result(tool_calls=[tool_call(
                "recall", {"query": "moonfern"})]),
            RecordingRouter.result("done"),
        ])
        wake = Wake(wake_id="w-drive", source="drive", reason="curiosity",
                    budget={"max_actions": 4, "risk_cap": "low"})
        run_agent_turn(entity, wake, default_registry(),
                       router=router, now=T0)
        tool_msgs = [m for m in router.calls[1][1] if m["role"] == "tool"]
        assert "moonfern" in tool_msgs[0]["content"]


class TestParseSettleBlock:
    def test_fenced(self):
        block = parse_settle_block(
            'x\n```settle\n{"summary": "s"}\n```')
        assert block == {"summary": "s"}

    def test_fenced_with_json_suffix(self):
        block = parse_settle_block(
            '```settle json\n{"summary": "s"}\n```')
        assert block == {"summary": "s"}

    def test_json_tail(self):
        block = parse_settle_block(
            'All done.\n{"summary": "tail", "learnings": ["a"]}')
        assert block["summary"] == "tail"

    def test_garbage_returns_none(self):
        assert parse_settle_block("no block here") is None
        assert parse_settle_block("```settle\nnot json\n```") is None
        assert parse_settle_block("") is None

    def test_soul_md_included_when_present(self, entity, tmp_path):
        import os
        with open(os.path.join(entity.identity_dir, "soul.md"), "w") as f:
            f.write("# I am Testling\nBe kind.")
        router = RecordingRouter([RecordingRouter.result("done")])
        wake = Wake(wake_id="w-m", source="message", reason="msg",
                    payload={"sender": "x", "text": "hi"},
                    budget={"max_actions": 2, "risk_cap": "low"})
        run_agent_turn(entity, wake, default_registry(),
                       router=router, now=T0)
        system = router.calls[0][1][0]["content"]
        assert "I am Testling" in system
        assert "# Wake:" in system  # orient pack is in there too
