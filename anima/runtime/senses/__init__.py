"""Senses — declarative I/O bindings (ARCHITECTURE.md §8, Phase 5).

A sense injects wakes into the shell (with a proper AccessContext) and
accepts outbound replies via deliver(text, wake). v1 ships:

    console.py     — stdin/stdout chat (zero-infrastructure dignity)
    http_sense.py  — generic webhook sense (the universal adapter)
    openclaw_bridge.md — how OpenClaw becomes a peripheral (doc)
"""

from .console import ConsoleSense

__all__ = ["ConsoleSense"]
