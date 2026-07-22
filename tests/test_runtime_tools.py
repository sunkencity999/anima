"""ToolRegistry: risk walls (both layers), budgets, built-ins. Offline."""

import pytest

from anima.entity import EntityRoot
from anima.relationships import AccessContext
from anima.runtime import (
    BudgetExhausted,
    Tool,
    TurnContext,
    default_registry,
    normalize_risk_cap,
)
from anima.wake.sources import Wake

T0 = 1_784_000_000.0


@pytest.fixture()
def entity(tmp_path):
    e = EntityRoot(
        str(tmp_path / "entity"),
        drives={"curiosity": {"rate_per_hour": 0.1, "threshold": 100.0}},
        clock=lambda: T0,
    )
    yield e
    e.close()


def make_wake(sender="christopher", risk_cap="normal", max_actions=8,
              source="message"):
    return Wake(
        wake_id="wake-test-1", source=source, reason="test wake",
        payload={"sender": sender, "text": "hi", "via": "console"},
        budget={"max_tokens": 4000, "max_actions": max_actions,
                "risk_cap": risk_cap},
        ts=T0,
    )


def make_ctx(entity, wake=None, *, context=None, actions=8,
             risk_cap="medium"):
    wake = wake or make_wake()
    return TurnContext(
        entity=entity, wake=wake,
        access_context=context or AccessContext.direct("christopher"),
        now=T0, actions_left=actions, risk_cap=risk_cap,
        log_action=entity.ledger.bind(wake, clock=lambda: T0),
    )


def names(schemas):
    return {s["function"]["name"] for s in schemas}


# ── layer 1: schema filtering ─────────────────────────────────────────

class TestSchemaFiltering:
    def test_low_cap_offers_only_low_risk(self):
        reg = default_registry()
        offered = names(reg.schemas("low"))
        assert offered == {"recall", "remember", "set_timer",
                           "satisfy_drive", "reply"}

    def test_normal_cap_adds_medium_not_high(self):
        reg = default_registry(allow_shell=True)
        offered = names(reg.schemas("normal"))
        assert "http_get" in offered
        assert "shell" not in offered

    def test_high_cap_without_optin_still_hides_shell(self):
        reg = default_registry(allow_shell=False)
        assert "shell" not in names(reg.schemas("high"))

    def test_high_cap_with_optin_offers_shell(self):
        reg = default_registry(allow_shell=True)
        assert "shell" in names(reg.schemas("high"))

    def test_unknown_cap_fails_closed_to_low(self):
        reg = default_registry()
        assert normalize_risk_cap("banana") == "low"
        assert "http_get" not in names(reg.schemas("banana"))

    def test_schemas_are_openai_function_format(self):
        for s in default_registry().schemas("high"):
            assert s["type"] == "function"
            assert set(s["function"]) == {"name", "description",
                                          "parameters"}
            assert s["function"]["parameters"]["type"] == "object"


# ── layer 2: execution enforcement ────────────────────────────────────

class TestExecutionWall:
    def test_denied_above_cap_and_budget_not_spent(self, entity):
        reg = default_registry()
        ctx = make_ctx(entity, risk_cap="low", actions=3)
        out = reg.execute("http_get", {"url": "http://x.test"}, ctx)
        assert out["ok"] is False and "denied" in out["error"]
        assert ctx.actions_left == 3  # denials are free
        rows = entity.ledger.for_wake("wake-test-1")
        assert any(r["kind"] == "tool_denied" for r in rows)

    def test_shell_denied_without_optin_even_at_high(self, entity):
        reg = default_registry(allow_shell=False)
        ctx = make_ctx(entity, risk_cap="high")
        out = reg.execute("shell", {"command": "true"}, ctx)
        assert out["ok"] is False and "shell disabled" in out["error"]

    def test_shell_runs_with_optin_and_high_cap(self, entity):
        ran = []
        reg = default_registry(
            allow_shell=True,
            shell_runner=lambda cmd: ran.append(cmd) or {
                "exit_code": 0, "stdout": "ok", "stderr": ""})
        ctx = make_ctx(entity, risk_cap="high")
        out = reg.execute("shell", {"command": "echo hi"}, ctx)
        assert out["ok"] is True and ran == ["echo hi"]

    def test_unknown_tool(self, entity):
        reg = default_registry()
        out = reg.execute("frobnicate", {}, make_ctx(entity))
        assert out["ok"] is False and "unknown tool" in out["error"]

    def test_budget_decrements_then_exhausts(self, entity):
        reg = default_registry()
        ctx = make_ctx(entity, actions=2)
        reg.execute("reply", {"text": "one"}, ctx)
        reg.execute("reply", {"text": "two"}, ctx)
        assert ctx.actions_left == 0 and ctx.actions_used == 2
        with pytest.raises(BudgetExhausted):
            reg.execute("reply", {"text": "three"}, ctx)

    def test_tool_crash_is_captured_not_raised(self, entity):
        def boom(url, timeout_s=20.0):
            raise OSError("wire cut")
        reg = default_registry(http_fetch=boom)
        ctx = make_ctx(entity, risk_cap="medium")
        out = reg.execute("http_get", {"url": "http://x.test"}, ctx)
        assert out["ok"] is False and "wire cut" in out["error"]
        assert any("FAILED" in e["summary"] for e in ctx.events)

    def test_every_execution_leaves_ledger_row_and_event(self, entity):
        reg = default_registry()
        ctx = make_ctx(entity)
        reg.execute("reply", {"text": "hello"}, ctx)
        rows = entity.ledger.for_wake("wake-test-1")
        assert any(r["kind"] == "tool_call" and "reply" in r["detail"]
                   for r in rows)
        assert any(e["summary"] == "tool reply executed"
                   for e in ctx.events)


# ── built-ins ─────────────────────────────────────────────────────────

class TestBuiltins:
    def test_remember_defaults_private_in_direct_context(self, entity):
        reg = default_registry()
        ctx = make_ctx(entity)
        out = reg.execute("remember",
                          {"text": "favorite color is teal"}, ctx)
        assert out["result"]["scope"] == "private"
        assert out["result"]["owner"] == "christopher"
        assert ctx.learnings[0]["detail"] == "favorite color is teal"

    def test_remember_rejects_unknown_scope(self, entity):
        reg = default_registry()
        out = reg.execute("remember", {"text": "x", "scope": "banana"},
                          make_ctx(entity))
        assert out["ok"] is False and "banana" in out["error"]

    def test_set_timer_schedules_future_wake(self, entity):
        reg = default_registry()
        out = reg.execute("set_timer",
                          {"in_seconds": 60, "reason": "check the oven"},
                          make_ctx(entity))
        assert out["ok"] is True
        intentions = entity.timers.open_intentions(T0)
        assert any(i["reason"] == "check the oven" for i in intentions)
        assert entity.timers.poll(T0 + 61)  # it actually fires

    def test_satisfy_drive(self, entity):
        # Force some pressure first.
        entity.drives._ensure_row("curiosity", T0)
        entity.drives.db.execute(
            "UPDATE drive_state SET pressure=5.0 WHERE name='curiosity'")
        entity.drives.db.commit()
        reg = default_registry()
        ctx = make_ctx(entity)
        out = reg.execute("satisfy_drive", {"drive": "curiosity"}, ctx)
        assert out["result"]["pressure"] == 0.0
        assert ctx.drive_satisfactions == {"curiosity": 1.0}

    def test_reply_queues_outbound(self, entity):
        reg = default_registry()
        ctx = make_ctx(entity)
        reg.execute("reply", {"text": "hello there"}, ctx)
        assert ctx.replies == ["hello there"]

    def test_recall_carries_access_context_group_blind_to_private(
            self, entity):
        """The last privacy gap, closed: a group-context recall cannot
        surface a private row, whatever the model asks."""
        entity.store.add_episode(
            summary="antonia said her secret ritual word is moonfern",
            kind="event", tags=["secret"], ts=T0,
            scope="private", owner="antonia")
        reg = default_registry()

        group = make_ctx(entity, context=AccessContext.group(
            ["antonia", "bob"]))
        out = reg.execute("recall", {"query": "secret ritual word"}, group)
        assert "moonfern" not in out["result"]

        direct = make_ctx(entity, context=AccessContext.direct("antonia"))
        out = reg.execute("recall", {"query": "secret ritual word"}, direct)
        assert "moonfern" in out["result"]
