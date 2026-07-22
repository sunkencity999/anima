"""Observatory v3b expression media: tone validation (accept + reject),
the express tool's medium choice, SVG escape-hatch hardening, and
kind-aware serve-time re-validation through the web sense."""

import json

import pytest

from anima.entity import EntityRoot
from anima.relationships import AccessContext
from anima.runtime import TurnContext, default_registry
from anima.runtime.sanitize import resanitize_expression, sanitize_fragment
from anima.runtime.tone import parse_tone_body, tone_to_body, validate_tone
from anima.wake.sources import Wake

T0 = 1_784_000_000.0


@pytest.fixture()
def entity(tmp_path):
    e = EntityRoot(str(tmp_path / "entity"), clock=lambda: T0)
    yield e
    e.close()


def make_ctx(entity, risk_cap="low", actions=8):
    wake = Wake(
        wake_id="wake-tone-1", source="message", reason="test",
        payload={"sender": "christopher", "text": "sing", "via": "web"},
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


GOOD_TONE = {
    "tempo": 96, "wave": "sine",
    "notes": [
        {"pitch": "C4", "dur": 1, "vel": 0.8},
        {"pitch": "E4", "dur": 0.5},
        {"pitch": "rest", "dur": 0.5},
        {"pitch": 67, "dur": 2, "vel": 0.5},
    ],
}


class TestToneValidation:
    def test_accepts_note_names_midi_and_rests(self):
        doc = validate_tone(GOOD_TONE)
        assert doc["medium"] == "tone"
        assert doc["tempo"] == 96 and doc["wave"] == "sine"
        pitches = [n["pitch"] for n in doc["notes"]]
        assert pitches == [60, 64, None, 67]     # C4=60, E4=64, G4=67
        assert doc["notes"][1]["vel"] == 0.7     # default velocity

    def test_accepts_accidentals(self):
        doc = validate_tone({"tempo": 120, "wave": "triangle",
                             "notes": [{"pitch": "F#3", "dur": 1},
                                       {"pitch": "Bb4", "dur": 1}]})
        assert [n["pitch"] for n in doc["notes"]] == [54, 70]

    def test_accepts_json_string_input(self):
        doc = validate_tone(json.dumps(GOOD_TONE))
        assert len(doc["notes"]) == 4

    def test_canonical_roundtrip(self):
        body = tone_to_body(validate_tone(GOOD_TONE))
        again = parse_tone_body(body)
        assert again == validate_tone(GOOD_TONE)

    @pytest.mark.parametrize("bad,msg", [
        ({"tempo": 10, "wave": "sine",
          "notes": [{"pitch": 60, "dur": 1}]}, "tempo"),
        ({"tempo": 999, "wave": "sine",
          "notes": [{"pitch": 60, "dur": 1}]}, "tempo"),
        ({"tempo": 120, "wave": "noise",
          "notes": [{"pitch": 60, "dur": 1}]}, "wave"),
        ({"tempo": 120, "wave": "sine", "notes": []}, "notes"),
        ({"tempo": 120, "wave": "sine", "notes": "la la"}, "notes"),
        ({"tempo": 120, "wave": "sine",
          "notes": [{"pitch": 300, "dur": 1}]}, "pitch"),
        ({"tempo": 120, "wave": "sine",
          "notes": [{"pitch": "H9", "dur": 1}]}, "pitch"),
        ({"tempo": 120, "wave": "sine",
          "notes": [{"pitch": 60, "dur": 0}]}, "dur"),
        ({"tempo": 120, "wave": "sine",
          "notes": [{"pitch": 60, "dur": 99}]}, "dur"),
        ({"tempo": 120, "wave": "sine",
          "notes": [{"pitch": 60, "dur": 1, "vel": 2}]}, "vel"),
        ({"tempo": 120, "wave": "sine",
          "notes": [{"pitch": 60, "dur": 1,
                     "vel": "loud"}]}, "vel"),
        ("not json {", "JSON"),
        ([1, 2, 3], "object"),
    ])
    def test_rejections(self, bad, msg):
        with pytest.raises(ValueError, match=msg):
            validate_tone(bad)

    def test_too_many_notes_rejected(self):
        notes = [{"pitch": 60, "dur": 0.1}] * 65
        with pytest.raises(ValueError, match="notes exceeds max"):
            validate_tone({"tempo": 120, "wave": "sine", "notes": notes})

    def test_total_duration_cap(self):
        # 40 beats at 40 bpm = 60 s > 30 s cap
        notes = [{"pitch": 60, "dur": 8}] * 5
        with pytest.raises(ValueError, match="cap is 30"):
            validate_tone({"tempo": 40, "wave": "sine", "notes": notes})

    def test_no_extra_keys_leak_into_canonical_form(self):
        doc = validate_tone({"tempo": 100, "wave": "sine",
                             "notes": [{"pitch": 60, "dur": 1,
                                        "onload": "alert(1)"}],
                             "script": "<script>x</script>"})
        assert set(doc) == {"medium", "tempo", "wave", "notes"}
        assert set(doc["notes"][0]) == {"pitch", "dur", "vel"}
        assert "script" not in tone_to_body(doc)

    def test_parse_tone_body_never_raises(self):
        assert parse_tone_body("") is None
        assert parse_tone_body("{broken") is None
        assert parse_tone_body(json.dumps({"tempo": 9999})) is None
        assert parse_tone_body("x" * 100_000) is None


class TestExpressToneMedium:
    def test_tone_expression_stored_canonical(self, entity):
        reg = default_registry()
        out = reg.execute("express", {"title": "a small phrase",
                                      "tone": GOOD_TONE},
                          make_ctx(entity))
        assert out["ok"], out
        assert out["result"]["kind"] == "tone"
        row = entity.store.recent_expressions()[0]
        assert row["kind"] == "tone"
        doc = json.loads(row["body"])
        assert doc["medium"] == "tone"
        assert doc["notes"][0]["pitch"] == 60

    def test_invalid_tone_rejected_before_storage(self, entity):
        reg = default_registry()
        out = reg.execute("express", {"tone": {"tempo": 120,
                                               "wave": "kazoo",
                                               "notes": [{"pitch": 60,
                                                          "dur": 1}]}},
                          make_ctx(entity))
        assert not out["ok"] and "wave" in out["error"]
        assert entity.store.recent_expressions() == []

    def test_exactly_one_medium_required(self, entity):
        reg = default_registry()
        out = reg.execute("express", {"html": "<p>hi</p>",
                                      "tone": GOOD_TONE},
                          make_ctx(entity))
        assert not out["ok"] and "exactly one" in out["error"]
        out = reg.execute("express", {"title": "nothing"},
                          make_ctx(entity))
        assert not out["ok"]

    def test_svg_path_expression_survives(self, entity):
        reg = default_registry()
        svg = ('<svg viewBox="0 0 100 100">'
               '<path d="M10 80 Q 52 10 95 80" stroke="#5eead4" '
               'fill="none" stroke-linecap="round" '
               'stroke-dasharray="4 2"/></svg>')
        out = reg.execute("express", {"svg": svg}, make_ctx(entity))
        assert out["ok"], out
        body = entity.store.recent_expressions()[0]["body"]
        assert '<path d="M10 80 Q 52 10 95 80"' in body
        assert 'stroke-linecap="round"' in body
        assert 'stroke-dasharray="4 2"' in body

    def test_store_rejects_unknown_kind(self, entity):
        with pytest.raises(ValueError):
            entity.store.add_expression("x", kind="wav")


class TestSvgEscapeHatches:
    """The v3b sanitizer hardening: SVG's HTML re-entry and reference
    mechanisms are dropped WITH their contents."""

    def test_foreignobject_dropped_with_contents(self):
        out = sanitize_fragment(
            '<svg><foreignObject><body onload="alert(1)">pwn</body>'
            '</foreignObject><circle cx="1" cy="1" r="1"/></svg>')
        assert "foreignobject" not in out.lower()
        assert "pwn" not in out and "alert" not in out
        assert "<circle" in out

    def test_use_and_image_dropped(self):
        out = sanitize_fragment(
            '<svg><use href="http://evil#x"/>'
            '<image href="http://evil/x.svg"/></svg>')
        assert "use" not in out and "image" not in out
        assert "evil" not in out

    def test_animate_family_dropped(self):
        out = sanitize_fragment(
            '<svg><animate attributeName="href" to="javascript:x()"/>'
            '<set attributeName="onclick" to="x()"/>'
            '<animateTransform attributeName="transform"/>'
            '<rect x="1" y="1" width="2" height="2"/></svg>')
        assert "animate" not in out.lower() and "set" not in out
        assert "javascript" not in out
        assert "<rect" in out

    def test_event_handler_attrs_still_dropped_on_svg(self):
        out = sanitize_fragment(
            '<svg onload="x()"><path d="M0 0" onclick="y()"/></svg>')
        assert "onload" not in out and "onclick" not in out

    def test_presentation_attrs_screened_for_url(self):
        out = sanitize_fragment(
            '<svg><rect width="4" height="4" '
            'fill="url(#grad)" stroke-dasharray="2 1"/></svg>')
        assert "url(" not in out
        assert 'stroke-dasharray="2 1"' in out

    def test_preserveaspectratio_canonical_casing(self):
        out = sanitize_fragment(
            '<svg preserveAspectRatio="xMidYMid meet" '
            'viewBox="0 0 4 4"></svg>')
        assert 'preserveAspectRatio="xMidYMid meet"' in out
        assert 'viewBox="0 0 4 4"' in out


class TestServeTimeResanitize:
    def test_tone_row_revalidated(self):
        row = {"kind": "tone",
               "body": tone_to_body(validate_tone(GOOD_TONE))}
        out = resanitize_expression(dict(row))
        assert json.loads(out["body"])["medium"] == "tone"

    def test_corrupt_tone_row_serves_empty(self):
        # A tone body tampered into markup must NOT reach the page.
        row = {"kind": "tone", "body": "<script>alert(1)</script>"}
        out = resanitize_expression(row)
        assert out["body"] == ""

    def test_markup_row_resanitized(self):
        row = {"kind": "html",
               "body": '<div onclick="x()">hi<script>y()</script></div>'}
        out = resanitize_expression(row)
        assert out["body"] == "<div>hi</div>"
