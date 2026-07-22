"""ANIMA routing — model failover as a verified contract (ARCHITECTURE.md §3).

Standalone-usable: importable without anima.memory / anima.wake. Optionally
logs failover events to a Phase-2 Ledger if one is injected into the Router.

The three bug classes this layer makes structurally impossible:
1. Empty reply classified as success → chain terminates with nothing.
2. Provider-shaped error bodies (Anthropic 400 JSON without HTTP context)
   classified as retryable-same instead of failover.
3. Hard "model does not exist" errors (Azure DeploymentNotFound) marked
   candidate_succeeded.
"""

from .policy import Candidate, TierPolicy, RoutingPolicy
from .contract import ContractResult, Reason, verify_response
from .classify import Decision, Classification, classify_error
from .router import (
    Router,
    RoutedResult,
    AttemptRecord,
    RoutingExhausted,
    TransportError,
    TransportResult,
    urllib_transport,
)

__all__ = [
    "Candidate",
    "TierPolicy",
    "RoutingPolicy",
    "ContractResult",
    "Reason",
    "verify_response",
    "Decision",
    "Classification",
    "classify_error",
    "Router",
    "RoutedResult",
    "AttemptRecord",
    "RoutingExhausted",
    "TransportError",
    "TransportResult",
    "urllib_transport",
]
