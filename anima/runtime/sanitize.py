"""HTML/SVG sanitizer for the expression surface (Phase 6b).

The model gets to *draw* — emit small HTML or SVG fragments that the
Observatory renders as cards. That is an injection surface by
definition, so the wall is structural, same principle as the ACLs: a
strict WHITELIST of tags and attributes, everything else dropped.

Pure stdlib (html.parser). Deny by default:

- tags not in ALLOWED_TAGS are dropped. Dangerous *containers*
  (script/style/iframe/object/embed/...) are dropped WITH their
  contents; benign unknown tags keep their children (an <a> becomes
  its text).
- attributes not in ALLOWED_ATTRS are dropped; on* handlers can never
  match the whitelist anyway.
- attribute VALUES are screened: `javascript:` (and any other URI
  scheme smuggling), `expression(`, and `url(` are rejected — the
  attribute is dropped. style attributes additionally forbid
  `@import`, `position:fixed` isn't blocked (harmless inside a card),
  but url() is, so no exfiltration beacons via background-image.
- text is re-escaped on output; comments, processing instructions and
  doctype declarations are dropped.

`sanitize_fragment()` is idempotent: sanitizing sanitized output is a
no-op (tested).
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

ALLOWED_TAGS = frozenset({
    # document-ish
    "div", "span", "p", "b", "i", "em", "strong",
    "h1", "h2", "h3", "h4",
    "ul", "ol", "li", "br", "hr",
    # svg
    "svg", "circle", "rect", "line", "path", "text", "g",
    "polygon", "polyline", "ellipse",
})

# Void elements: emitted self-closed, never pushed on the open stack.
_VOID_TAGS = frozenset({"br", "hr"})

# html.parser lowercases attribute names; SVG needs canonical casing
# for some of them to actually work in the browser.
_ATTR_CANONICAL = {
    "viewbox": "viewBox",
    "preserveaspectratio": "preserveAspectRatio",
}

ALLOWED_ATTRS = frozenset({
    "style", "class", "width", "height", "viewbox", "fill", "stroke",
    "stroke-width", "cx", "cy", "r", "x", "y", "x1", "y1", "x2", "y2",
    "d", "points", "font-size", "text-anchor", "transform", "opacity",
    "rx", "ry",
    # SVG presentation attrs for richer path drawing (v3b) — pure
    # styling, no reference/URI semantics, screened by _BAD_VALUE
    # like everything else.
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
    "stroke-dashoffset", "fill-opacity", "stroke-opacity",
    "fill-rule", "stroke-miterlimit", "preserveaspectratio",
})

# Tags whose CONTENT is dangerous, not just the tag itself. Everything
# between <script>...</script> etc. is swallowed.
_DROP_CONTENT_TAGS = frozenset({
    "script", "style", "iframe", "object", "embed", "link", "img",
    "meta", "base", "form", "input", "button", "textarea", "select",
    "video", "audio", "source", "template", "noscript", "frame",
    "frameset", "applet", "math", "title", "head",
    # SVG escape hatches: foreignObject re-enters HTML land, use/image
    # reference external content, animate/set can rewrite attributes
    # (including href on browsers that still honor xlink). All dropped
    # WITH contents — deny by default.
    "foreignobject", "use", "animate", "set", "animatetransform",
    "animatemotion", "mpath",
})

# Value screening: anything matching these kills the attribute.
_BAD_VALUE = re.compile(
    r"(?:javascript|vbscript|data)\s*:"     # scheme smuggling
    r"|url\s*\("                            # CSS url() beacons
    r"|expression\s*\("                     # legacy IE CSS execution
    r"|@import"                             # CSS import
    r"|&#",                                 # entity-encoded smuggling
    re.IGNORECASE,
)
_WS_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _value_ok(value: str) -> bool:
    # Collapse whitespace/control chars first: "java\nscript:" is still
    # javascript: to a browser.
    collapsed = _WS_CONTROL.sub("", value or "")
    return not _BAD_VALUE.search(collapsed)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.open_stack: List[str] = []   # allowed tags awaiting close
        self.drop_depth = 0               # >0 → inside a dropped container

    # ── emit helpers ──────────────────────────────────────────────────
    def _emit_start(self, tag: str,
                    attrs: List[Tuple[str, Optional[str]]],
                    self_closing: bool) -> None:
        parts = [tag]
        for name, value in attrs:
            name = name.lower()
            if name not in ALLOWED_ATTRS:
                continue
            value = value if value is not None else ""
            if not _value_ok(value):
                continue
            canonical = _ATTR_CANONICAL.get(name, name)
            parts.append(f'{canonical}="{_html.escape(value, quote=True)}"')
        if self_closing or tag in _VOID_TAGS:
            self.out.append(f"<{' '.join(parts)}/>")
        else:
            self.out.append(f"<{' '.join(parts)}>")
            self.open_stack.append(tag)

    # ── parser callbacks ──────────────────────────────────────────────
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.drop_depth:
            if tag in _DROP_CONTENT_TAGS:
                self.drop_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS:
            self.drop_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            return  # drop tag, keep children
        self._emit_start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.drop_depth or tag in _DROP_CONTENT_TAGS:
            return
        if tag not in ALLOWED_TAGS:
            return
        self._emit_start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.drop_depth:
            if tag in _DROP_CONTENT_TAGS:
                self.drop_depth -= 1
            return
        if tag not in ALLOWED_TAGS:
            return
        # Close up to the matching open tag (tolerates sloppy nesting).
        if tag in self.open_stack:
            while self.open_stack:
                top = self.open_stack.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break

    def handle_data(self, data):
        if self.drop_depth:
            return
        self.out.append(_html.escape(data))

    # comments / declarations / PIs: dropped
    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def result(self) -> str:
        # Auto-close anything the fragment left open.
        while self.open_stack:
            self.out.append(f"</{self.open_stack.pop()}>")
        return "".join(self.out)


def resanitize_expression(row: dict) -> dict:
    """Serve-time defense in depth for one expression row, kind-aware:
    tone bodies re-validate through the tone schema (anything invalid
    serves as empty), markup bodies pass back through the sanitizer.
    Mutates and returns the row."""
    from .tone import parse_tone_body, tone_to_body

    if row.get("kind") == "tone":
        doc = parse_tone_body(row.get("body") or "")
        row["body"] = tone_to_body(doc) if doc else ""
    else:
        row["body"] = sanitize_fragment(row.get("body") or "")
    return row


MAX_FRAGMENT_CHARS = 32_768


def sanitize_fragment(fragment: str,
                      max_chars: int = MAX_FRAGMENT_CHARS) -> str:
    """Sanitize an untrusted HTML/SVG fragment down to the whitelist.

    Returns safe markup (possibly empty). Never raises on weird input —
    a sanitizer that crashes on hostile input is a denial-of-service.
    """
    if not fragment:
        return ""
    fragment = str(fragment)[:max_chars]
    parser = _Sanitizer()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:
        # html.parser is robust, but the wall must hold regardless:
        # on any parse explosion the fragment is fully escaped text.
        return _html.escape(fragment)
    return parser.result()
