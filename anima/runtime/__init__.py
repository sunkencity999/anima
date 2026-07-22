"""ANIMA runtime shell — Phase 5 (docs/PHASE5_RUNTIME.md, Build Order #5).

The organism breathes wall-clock air: a process that hosts an EntityRoot,
runs wakes in real time, ACTS through a model, and wears external
channels as senses.

Public surface:
    ToolRegistry, Tool, default_registry   — risk-tiered hands (tools.py)
    TurnContext, BudgetExhausted
    run_agent_turn, attach_agent_turn      — the act phase (agent_turn.py)
    tier_for_wake, parse_settle_block
    RuntimeShell, PidLock                  — the process (shell.py)
"""

from .tools import (
    BudgetExhausted,
    Tool,
    ToolRegistry,
    TurnContext,
    default_registry,
    normalize_risk_cap,
)
from .agent_turn import (
    attach_agent_turn,
    parse_settle_block,
    run_agent_turn,
    tier_for_wake,
)
from .shell import PidLock, RuntimeShell

__all__ = [
    "BudgetExhausted",
    "Tool",
    "ToolRegistry",
    "TurnContext",
    "default_registry",
    "normalize_risk_cap",
    "attach_agent_turn",
    "parse_settle_block",
    "run_agent_turn",
    "tier_for_wake",
    "PidLock",
    "RuntimeShell",
]
