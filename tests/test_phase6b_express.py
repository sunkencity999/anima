"""Phase 6b express tool: sanitize-then-store, retrievable, budgeted."""

import pytest

from anima.entity import EntityRoot
from anima.relationships import AccessContext
from anima.runtime import TurnContext, default_registry
from anima.wake.sources import Wake

T0 = 1_784_000_000.0


@pytest.fixture()
def entity(tmp_path):
    e = EntityRoot(str(tmp_path / "entity"), clock=lambda: T0)
    yield e
    e.close()


def make_ctx(entity, risk_cap="low", actions=8):
    wake = Wake(
        wake_id="wake-expr-1", source="message", reason="test",
        payload={"sender": "christopher", "text": "draw", "via": "web"},
        budget={"max_tokens": 4000, "max_actions": actions,
                "risk_cap": risk_cap},
        ts=T0,
    )
    return TurnContext(
        entity=entity, wake=wake,
        access_context=AccessContext.direct("christopher"),
        now=T0, actions_left=actions, risk_cap=risk_cap,
        log_action=entity.ledger.bind(wake, clock=lambda: T0),
    )


class TestExpressTool:
    def test_offered_at_low_risk_cap(self):
        reg = default_registry()
        offered = {s["function"]["name"] for s in reg.schemas("low")}
        assert "express" in offered

    def test_html_expression_stored_and_retrievable(self, entity):
        reg = default_registry()
        out = reg.execute("express", {
            "title": "tonight's sky",
            "html": '<div style="color:#7fd4ff"><h3>clear</h3></div>',
        }, make_ctx(entity))
        assert out["ok"]
        rows = entity.store.recent_expressions()
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == "html"
        assert row["title"] == "tonight's sky"
        assert row["wake_id"] == "wake-expr-1"
        assert row["ts"] == T0
        assert "<h3>clear</h3>" in row["body"]

    def test_svg_expression_stored(self, entity):
        reg = default_registry()
        out = reg.execute("express", {
            "svg": '<svg viewBox="0 0 10 10">'
                   '<circle cx="5" cy="5" r="4" fill="gold"/></svg>',
        }, make_ctx(entity))
        assert out["ok"] and out["result"]["kind"] == "svg"
        row = entity.store.recent_expressions()[0]
        assert row["kind"] == "svg"
        assert 'viewBox="0 0 10 10"' in row["body"]

    def test_hostile_html_sanitized_before_storage(self, entity):
        reg = default_registry()
        out = reg.execute("express", {
            "html": '<div onclick="pwn()">hi'
                    "<script>fetch('http://evil')</script></div>",
        }, make_ctx(entity))
        assert out["ok"]
        body = entity.store.recent_expressions()[0]["body"]
        assert "script" not in body and "onclick" not in body
        assert "hi" in body
        assert out["result"]["sanitized"] is True

    def test_expression_entirely_hostile_fails(self, entity):
        reg = default_registry()
        out = reg.execute("express", {
            "html": "<script>alert(1)</script>",
        }, make_ctx(entity))
        assert not out["ok"]
        assert entity.store.recent_expressions() == []

    def test_svg_that_loses_its_root_fails(self, entity):
        reg = default_registry()
        out = reg.execute("express", {"svg": "<b>not svg</b>"},
                          make_ctx(entity))
        assert not out["ok"] and "svg" in out["error"]

    def test_requires_exactly_one_body(self, entity):
        reg = default_registry()
        assert not reg.execute("express", {}, make_ctx(entity))["ok"]
        assert not reg.execute("express", {
            "html": "<p>a</p>", "svg": "<svg></svg>"},
            make_ctx(entity))["ok"]

    def test_spends_one_action(self, entity):
        reg = default_registry()
        ctx = make_ctx(entity, actions=2)
        reg.execute("express", {"html": "<p>x</p>"}, ctx)
        assert ctx.actions_left == 1
        assert ctx.actions_used == 1

    def test_recent_expressions_orders_newest_first(self, entity):
        for i in range(3):
            entity.store.add_expression(f"<p>{i}</p>", ts=T0 + i)
        rows = entity.store.recent_expressions(limit=2)
        assert len(rows) == 2
        assert "<p>2</p>" == rows[0]["body"]

    def test_store_rejects_bad_kind_and_empty_body(self, entity):
        with pytest.raises(ValueError):
            entity.store.add_expression("<p>x</p>", kind="pdf")
        with pytest.raises(ValueError):
            entity.store.add_expression("")
