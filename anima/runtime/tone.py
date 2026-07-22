"""Tone expressions (Observatory v3b) — validated sound as data.

The entity can compose a short tone sequence: a tempo, a waveform and
a list of notes. Nothing binary is ever stored or shipped — the
"sound" is structured JSON, validated down to a strict numeric schema
here (server side, before storage AND again at serve time), and the
Observatory page renders/plays it with WebAudio from the validated
numbers. The same wall philosophy as the HTML sanitizer: whitelist,
bounded, deny by default — a tone body is either exactly the canonical
schema or it does not exist.

Canonical form (what `validate_tone` returns / what gets stored):

    {"medium": "tone",
     "tempo": 40..240,               # beats per minute (int)
     "wave":  "sine" | "triangle" | "square" | "sawtooth",
     "notes": [{"pitch": 21..108 | null,   # MIDI number; null = rest
                "dur":   0.05..16.0,       # beats
                "vel":   0.0..1.0},        # velocity/gain
               ...]}                       # 1..64 notes

Input is friendlier than the canonical form: `pitch` also accepts
note names ("C4", "F#3", "Bb5", "rest"), and `dur`/`vel` accept any
numeric. Everything else is rejected with a ValueError that names the
offence — the model gets honest feedback, not silence.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

ALLOWED_WAVES = ("sine", "triangle", "square", "sawtooth")

MIN_TEMPO, MAX_TEMPO = 40, 240
MIN_PITCH, MAX_PITCH = 21, 108          # piano range, MIDI numbers
MIN_DUR, MAX_DUR = 0.05, 16.0           # beats
MAX_NOTES = 64
MAX_TOTAL_SECONDS = 30.0                # a phrase, not a broadcast
MAX_BODY_CHARS = 8192                   # serve-time parse guard

_NOTE_RE = re.compile(r"^([A-Ga-g])([#b♯♭]?)(-?\d{1,2})$")
_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _pitch_to_midi(value: Any) -> Optional[int]:
    """→ MIDI number in range, None for a rest. Raises on nonsense."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s.lower() in ("rest", "r", ""):
            return None
        m = _NOTE_RE.match(s)
        if not m:
            raise ValueError(f"unparseable pitch {value!r} "
                             f"(want e.g. 'C4', 'F#3', 60, or 'rest')")
        letter, accidental, octave = m.groups()
        midi = 12 * (int(octave) + 1) + _SEMITONE[letter.upper()]
        if accidental in ("#", "♯"):
            midi += 1
        elif accidental in ("b", "♭"):
            midi -= 1
        value = midi
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"pitch must be a note name, MIDI number or "
                         f"null/'rest', got {type(value).__name__}")
    midi = int(round(float(value)))
    if not (MIN_PITCH <= midi <= MAX_PITCH):
        raise ValueError(f"pitch {midi} outside MIDI range "
                         f"{MIN_PITCH}..{MAX_PITCH}")
    return midi


def _num(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, "
                         f"got {type(value).__name__}")
    return float(value)


def validate_tone(doc: Any) -> dict:
    """Validate an untrusted tone document into canonical form.

    Deny by default: raises ValueError on anything outside the schema.
    """
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError as exc:
            raise ValueError(f"tone is not valid JSON: {exc}") from None
    if not isinstance(doc, dict):
        raise ValueError("tone must be a JSON object")

    tempo = int(round(_num(doc.get("tempo", 120), "tempo")))
    if not (MIN_TEMPO <= tempo <= MAX_TEMPO):
        raise ValueError(f"tempo {tempo} outside {MIN_TEMPO}..{MAX_TEMPO}")

    wave = str(doc.get("wave", "sine")).lower().strip()
    if wave not in ALLOWED_WAVES:
        raise ValueError(f"wave {wave!r} not in {ALLOWED_WAVES}")

    notes_in = doc.get("notes")
    if not isinstance(notes_in, list) or not notes_in:
        raise ValueError("tone requires a non-empty notes list")
    if len(notes_in) > MAX_NOTES:
        raise ValueError(f"{len(notes_in)} notes exceeds max {MAX_NOTES}")

    notes = []
    total_beats = 0.0
    for i, n in enumerate(notes_in):
        if not isinstance(n, dict):
            raise ValueError(f"note {i} must be an object")
        pitch = _pitch_to_midi(n.get("pitch"))
        dur = _num(n.get("dur", 1.0), f"note {i} dur")
        if not (MIN_DUR <= dur <= MAX_DUR):
            raise ValueError(f"note {i} dur {dur} outside "
                             f"{MIN_DUR}..{MAX_DUR} beats")
        vel = _num(n.get("vel", 0.7), f"note {i} vel")
        if not (0.0 <= vel <= 1.0):
            raise ValueError(f"note {i} vel {vel} outside 0..1")
        total_beats += dur
        notes.append({"pitch": pitch, "dur": round(dur, 4),
                      "vel": round(vel, 4)})

    total_seconds = total_beats * 60.0 / tempo
    if total_seconds > MAX_TOTAL_SECONDS:
        raise ValueError(f"tone runs {total_seconds:.1f}s — cap is "
                         f"{MAX_TOTAL_SECONDS:.0f}s (a phrase, not a "
                         f"broadcast)")

    return {"medium": "tone", "tempo": tempo, "wave": wave,
            "notes": notes}


def tone_to_body(doc: dict) -> str:
    """Canonical storage form: compact JSON."""
    return json.dumps(doc, separators=(",", ":"), sort_keys=True)


def parse_tone_body(body: str) -> Optional[dict]:
    """Serve-time re-validation (defense in depth, mirrors the HTML
    re-sanitize pass): returns the canonical dict, or None if the
    stored body is not a valid tone. Never raises."""
    if not body or len(body) > MAX_BODY_CHARS:
        return None
    try:
        return validate_tone(body)
    except (ValueError, TypeError):
        return None
