"""Console sense — stdin/stdout chat (PHASE5_RUNTIME.md).

"For development and for the dignity of being able to talk to an entity
on a laptop with zero infrastructure."

Messages inject as direct-context wakes from a configurable person id.
I/O callables are injectable so tests run without a terminal.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ...relationships import AccessContext


class ConsoleSense:
    name = "console"

    def __init__(
        self,
        sender: str = "operator",
        *,
        channel: str = "console",
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ):
        self.sender = sender
        self.channel = channel
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.shell: Any = None

    # ── shell lifecycle hooks ─────────────────────────────────────────
    def start(self, shell: Any) -> None:
        self.shell = shell

    def stop(self) -> None:
        self.shell = None

    # ── outbound ──────────────────────────────────────────────────────
    def deliver(self, text: str, wake: Any = None) -> None:
        self.output_fn(text)

    # ── inbound ───────────────────────────────────────────────────────
    def inject(self, shell: Any, text: str):
        ctx = AccessContext.direct(self.sender, channel=self.channel)
        return shell.inject_message(self.sender, text,
                                    context=ctx, via="console")

    def run_interactive(self, shell: Any) -> None:
        """Blocking chat loop: read line → inject → dispatch → replies
        arrive via deliver(). EOF (Ctrl-D) or '/quit' exits."""
        self.shell = shell
        while not shell.stopping:
            try:
                line = self.input_fn("you> ")
            except EOFError:
                break
            line = (line or "").strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            self.inject(shell, line)
            shell.run_pending_once()
