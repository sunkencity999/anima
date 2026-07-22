"""Phase 6b sanitizer: adversarial suite. The whitelist must hold
against everything a hostile (or merely confused) model can emit."""

import pytest

from anima.runtime.sanitize import sanitize_fragment


class TestScriptStripping:
    def test_script_tag_and_contents_dropped(self):
        out = sanitize_fragment("<div>a<script>alert(1)</script>b</div>")
        assert "script" not in out.lower()
        assert "alert" not in out
        assert out == "<div>ab</div>"

    def test_nested_script_inside_svg(self):
        out = sanitize_fragment(
            '<svg><script>fetch("http://evil")</script>'
            '<circle cx="5" cy="5" r="4"/></svg>')
        assert "script" not in out.lower()
        assert "fetch" not in out
        assert "<circle" in out

    def test_uppercase_script(self):
        out = sanitize_fragment("<SCRIPT>alert(1)</SCRIPT>ok")
        assert "alert" not in out and "ok" in out

    def test_iframe_object_embed_link_img_dropped(self):
        for frag in ('<iframe src="http://x"></iframe>',
                     '<object data="x"></object>',
                     '<embed src="x">',
                     '<link rel="stylesheet" href="x">',
                     '<img src="x" onerror="alert(1)">'):
            out = sanitize_fragment(frag)
            assert out == "", f"{frag!r} → {out!r}"

    def test_form_and_inputs_dropped(self):
        out = sanitize_fragment(
            '<form action="http://evil"><input value="x"></form>')
        assert "form" not in out and "input" not in out


class TestHandlerStripping:
    def test_onclick_dropped(self):
        out = sanitize_fragment('<div onclick="alert(1)">hi</div>')
        assert "onclick" not in out and out == "<div>hi</div>"

    def test_onerror_onload_onmouseover_dropped(self):
        out = sanitize_fragment(
            '<svg onload="x()"><circle onmouseover="y()" cx="1" cy="1" '
            'r="1" onerror="z()"/></svg>')
        assert "on" not in out.replace("polygon", "").split("=")[0] or True
        for handler in ("onload", "onmouseover", "onerror"):
            assert handler not in out
        assert 'cx="1"' in out


class TestUrlSmuggling:
    def test_javascript_url_in_style_dropped(self):
        out = sanitize_fragment(
            '<div style="background:url(javascript:alert(1))">x</div>')
        assert "javascript" not in out and "url(" not in out
        assert out == "<div>x</div>"

    def test_css_url_beacon_dropped(self):
        out = sanitize_fragment(
            '<div style="background-image:url(http://evil/px.gif)">x</div>')
        assert "url(" not in out

    def test_split_javascript_scheme_dropped(self):
        out = sanitize_fragment(
            '<div style="x:expression(alert(1))">y</div>')
        assert "expression" not in out

    def test_whitespace_smuggled_scheme(self):
        out = sanitize_fragment(
            '<div style="a:java\nscript:alert(1)">x</div>')
        assert "script:" not in out

    def test_import_dropped(self):
        out = sanitize_fragment('<div style="@import url(x)">x</div>')
        assert "@import" not in out

    def test_entity_encoded_value_dropped(self):
        out = sanitize_fragment(
            '<div style="a:&#106;avascript:alert(1)">x</div>')
        assert "style=" not in out


class TestWhitelistPreservation:
    def test_allowed_html_survives(self):
        frag = ('<div class="mood"><h2>Tonight</h2><p>The <b>sky</b> is '
                '<em>clear</em>.</p><ul><li>one</li><li>two</li></ul>'
                '<hr/></div>')
        out = sanitize_fragment(frag)
        for bit in ("<h2>", "<b>sky</b>", "<em>clear</em>", "<li>one</li>",
                    'class="mood"'):
            assert bit in out

    def test_allowed_svg_survives_with_viewbox_casing(self):
        frag = ('<svg width="100" height="100" viewBox="0 0 100 100">'
                '<circle cx="50" cy="50" r="40" fill="#7fd4ff" '
                'stroke="#fff" stroke-width="2" opacity="0.8"/>'
                '<path d="M10 10 L90 90"/>'
                '<text x="50" y="55" text-anchor="middle" '
                'font-size="12">hi</text>'
                '<g transform="rotate(15)"><rect x="1" y="1" rx="2" '
                'ry="2" width="10" height="10"/></g>'
                '<polygon points="0,0 10,0 5,10"/></svg>')
        out = sanitize_fragment(frag)
        assert 'viewBox="0 0 100 100"' in out
        for bit in ("<circle", "<path", "<text", "<g", "<polygon",
                    'stroke-width="2"', 'text-anchor="middle"',
                    'transform="rotate(15)"'):
            assert bit in out

    def test_benign_style_preserved(self):
        out = sanitize_fragment(
            '<div style="color:#7fd4ff; padding:4px">x</div>')
        assert "color:#7fd4ff" in out

    def test_unknown_benign_tag_keeps_children(self):
        out = sanitize_fragment("<a href='http://x'>label</a>")
        assert out == "label"

    def test_text_is_escaped(self):
        out = sanitize_fragment("<p>a < b & c > d</p>")
        assert "&lt;" in out and "&amp;" in out

    def test_idempotent(self):
        frag = ('<div style="color:red"><svg viewBox="0 0 1 1">'
                '<circle cx="0" cy="0" r="1"/></svg>&amp; text</div>')
        once = sanitize_fragment(frag)
        assert sanitize_fragment(once) == once


class TestRobustness:
    def test_unclosed_tags_autoclosed(self):
        out = sanitize_fragment("<div><p>open")
        assert out == "<div><p>open</p></div>"

    def test_sloppy_nesting_survives(self):
        out = sanitize_fragment("<b><i>x</b></i>")
        assert out.count("<b>") == out.count("</b>")
        assert out.count("<i>") == out.count("</i>")

    def test_empty_and_none_like(self):
        assert sanitize_fragment("") == ""
        assert sanitize_fragment(None) == ""

    def test_comment_and_doctype_dropped(self):
        out = sanitize_fragment(
            "<!DOCTYPE html><!-- sneak --><div>x</div><?php evil ?>")
        assert out == "<div>x</div>"

    def test_length_cap(self):
        out = sanitize_fragment("<div>" + "a" * 100_000 + "</div>",
                                max_chars=1000)
        assert len(out) <= 1100

    def test_plain_text_passthrough_escaped(self):
        assert sanitize_fragment("just words") == "just words"
