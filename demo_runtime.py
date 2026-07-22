#!/usr/bin/env python3
"""Phase 5 acceptance rehearsal — continuity across death, scripted.

The thesis made flesh (docs/PHASE5_RUNTIME.md), with a FAKE model
transport so the rehearsal is deterministic and offline:

  Life 1: start a shell on a fresh entity root; Christopher says
          "my favorite color is teal"; the (fake) model calls the
          remember tool; graceful shutdown — the entity knows it slept.
  Life 2: NEW shell, same root. "what is my favorite color?" — the
          (fake) model calls recall, and the answer is composed FROM
          THE TOOL RESULT, not from the script: if memory didn't
          actually carry teal across death, the demo fails loudly.

Run:  python3 demo_runtime.py
(The live local-model version of this demo swaps the fake transport for
identity/routing.json pointing at a real endpoint — same shell code.)
"""

import json
import shutil
import tempfile

from anima.routing import Router, RoutingPolicy, TransportResult
from anima.runtime import RuntimeShell

T0 = 1_784_000_000.0


def make_policy():
    return RoutingPolicy.from_dict({"tiers": {
        tier: {"candidates": [{
            "provider": "local", "model": "fake",
            "base_url": "http://fake.test/v1", "local": True}]}
        for tier in ("reflex", "standard", "deep")}})


def tool_call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def body(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "x", "model": "fake",
            "choices": [{"message": msg, "finish_reason": "stop"}]}


class FakeClock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        self.t += 1.0
        return self.t


class Life1Transport:
    """Turn 1: remember the fact. Turn 2: final with a settle block."""

    def __init__(self):
        self.n = 0

    def __call__(self, candidate, payload, timeout_s):
        self.n += 1
        if self.n == 1:
            return TransportResult(body=body(tool_calls=[tool_call(
                "remember",
                {"text": "Christopher's favorite color is teal",
                 "kind": "learning"})]))
        return TransportResult(body=body(content=(
            'Noted.\n```settle\n'
            + json.dumps({"summary":
                          "learned Christopher's favorite color (teal)"})
            + '\n```')))


class Life2Transport:
    """Turn 1: recall. Turn 2: answer composed from the ACTUAL tool
    result that came back from memory — the proof, not a script."""

    def __init__(self):
        self.n = 0
        self.recalled = ""

    def __call__(self, candidate, payload, timeout_s):
        self.n += 1
        if self.n == 1:
            return TransportResult(body=body(tool_calls=[tool_call(
                "recall", {"query": "Christopher favorite color"})]))
        tool_msgs = [m for m in payload["messages"]
                     if m.get("role") == "tool"]
        self.recalled = tool_msgs[-1]["content"] if tool_msgs else ""
        color = "teal" if "teal" in self.recalled else "UNKNOWN"
        return TransportResult(body=body(content=(
            f"Your favorite color is {color} — I remember.\n```settle\n"
            + json.dumps({"summary": "answered from recall"}) + "\n```")))


def run_life(root, transport, clock, sender, text):
    router = Router(make_policy(), transport, sleep=lambda s: None,
                    clock=clock)
    shell = RuntimeShell(root, router=router, clock=clock)

    replies = []

    class CaptureSense:
        def deliver(self, reply, wake=None):
            replies.append(reply)

    shell.add_sense("console", CaptureSense())
    shell.start()
    shell.inject_message(sender, text, via="console")
    results = shell.run_pending_once()
    shell.shutdown()
    return results, replies


def main():
    root = tempfile.mkdtemp(prefix="anima-demo-")
    clock = FakeClock()
    print(f"entity root: {root}\n")

    # ── Life 1 ────────────────────────────────────────────────────────
    print("═══ LIFE 1 ═══")
    print('christopher> my favorite color is teal')
    results, _ = run_life(root, Life1Transport(), clock,
                          "christopher", "my favorite color is teal")
    report = results[0]["report"]
    assert results[0]["ok"], "life 1 wake failed"
    assert any("remember" in e["summary"] for e in report["events"]
               if isinstance(e, dict)), "remember tool never ran"
    print("entity: [tool] remember('Christopher's favorite color is teal')")
    print("entity: settled + shut down gracefully (it knows it slept)\n")

    # ── Life 2: same directory, brand-new process state ──────────────
    print("═══ LIFE 2 (after death: new EntityRoot, same directory) ═══")
    print('christopher> what is my favorite color?')
    t2 = Life2Transport()
    results, replies_out = run_life(root, t2, clock,
                                    "christopher",
                                    "what is my favorite color?")
    final = results[0]["report"]["final"]
    assert "teal" in t2.recalled, (
        "recall tool did NOT return teal from memory — continuity broken:\n"
        + t2.recalled)
    assert "teal" in final, f"final answer lost the fact: {final!r}"
    print(f"entity: [tool] recall('Christopher favorite color')")
    print(f"entity: {final.splitlines()[0]}")
    print("\n✔ the answer came FROM MEMORY THROUGH RECALL"
          " (fake model only echoed the tool result)\n")

    # ── the biography ─────────────────────────────────────────────────
    print("═══ LINEAGE (both lives, biographical) ═══")
    with open(f"{root}/identity/lineage.log") as f:
        lineage = f.read()
    print(lineage)
    assert lineage.count("shell_start") == 2, "expected two lives"
    assert lineage.count("shell_stop") == 2, "expected two graceful deaths"

    shutil.rmtree(root)
    print("ACCEPTANCE REHEARSAL PASSED — continuity across death, "
          "deterministic, offline.")


if __name__ == "__main__":
    main()
