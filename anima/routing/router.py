"""The router: walks the candidate chain under contract (ARCHITECTURE.md §3).

Flow per candidate:
  transport call
    ├─ transport error  → classify → retry_same (bounded, jittered
    │                     backoff) or failover_next
    └─ 200 response     → parse → contract.verify_response
                          ├─ pass → RoutedResult (chain terminates HERE
                          │         and ONLY here)
                          └─ fail → failover_next (a contract failure is
                                    never retried on the same candidate:
                                    same prompt + same model ≈ same hole)

Everything is injectable for tests: transport, sleep, clock, rng, ledger.
The default transport speaks OpenAI-compatible chat-completions via urllib.

Failover events are first-class telemetry: RoutedResult.degraded tells the
caller it did not get its first-choice model, and every attempt is carried
in RoutedResult.attempts / RoutingExhausted.attempts for audit.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

from .policy import Candidate, RoutingPolicy, TierPolicy
from .contract import ContractResult, Reason, verify_response
from .classify import Classification, Decision, classify_error


class TransportError(Exception):
    """A non-2xx or connection-level failure from a candidate endpoint."""

    def __init__(self, message: str, status: Optional[int] = None,
                 body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class TransportResult:
    """A 2xx response body from a candidate endpoint."""

    body: Dict[str, Any]
    status: int = 200


@dataclass
class AttemptRecord:
    """Audit record for one call to one candidate."""

    candidate: str            # provider/model
    provider: str
    model: str
    index: int                # position in the (ordered) chain
    try_number: int           # 1-based within this candidate
    ts: float
    outcome: str              # ok | contract_failed | transport_error
    reason: str = ""          # contract Reason or classify reason
    decision: str = ""        # retry_same | failover_next | "" on ok
    detail: str = ""
    latency_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RoutedResult:
    content: str
    tool_calls: Optional[List[Dict[str, Any]]]
    model_used: str
    provider: str
    attempts: List[AttemptRecord]
    degraded: bool
    failover_events: List[Dict[str, Any]]
    raw_body: Optional[Dict[str, Any]] = None


class RoutingExhausted(Exception):
    """Every candidate in the tier failed. Carries the full audit trail."""

    def __init__(self, tier: str, attempts: List[AttemptRecord]):
        self.tier = tier
        self.attempts = attempts
        lines = "; ".join(
            f"{a.candidate}#{a.try_number} {a.outcome}:{a.reason}"
            for a in attempts
        )
        super().__init__(
            f"routing exhausted for tier {tier!r} after "
            f"{len(attempts)} attempts: {lines}"
        )


# ── default transport ─────────────────────────────────────────────────

def urllib_transport(candidate: Candidate, payload: Dict[str, Any],
                     timeout_s: float) -> TransportResult:
    """OpenAI-compatible chat-completions POST via urllib (pure stdlib)."""
    url = candidate.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if candidate.api_key_env:
        key = os.environ.get(candidate.api_key_env, "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return TransportResult(body=body, status=resp.status)
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise TransportError(f"http {e.code} from {candidate.id}",
                             status=e.code, body=raw) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TransportError(f"connection to {candidate.id} failed: {e}",
                             status=None, body=str(e)) from e


def _parse_choice(body: Dict[str, Any]):
    """Extract (content, tool_calls, finish_reason) from a chat body."""
    choices = body.get("choices") or []
    if not choices:
        return "", None, None
    choice = choices[0] or {}
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    if isinstance(content, list):  # content-block arrays
        content = "".join(
            b.get("text", "") for b in content if isinstance(b, dict))
    tool_calls = msg.get("tool_calls") or None
    return content, tool_calls, choice.get("finish_reason")


# ── the router ────────────────────────────────────────────────────────

class Router:
    """Walks a tier's candidate chain under the response contract.

    Args:
        policy: RoutingPolicy.
        transport: callable(candidate, payload, timeout_s) -> TransportResult,
            raising TransportError on failure. Defaults to urllib.
        sleep/clock/rng: injectable for deterministic tests.
        ledger: optional Phase-2 Ledger (anima.wake.ledger). Failover events
            and exhaustion are logged there when provided. The routing layer
            never imports the ledger itself — it stays standalone.
    """

    def __init__(
        self,
        policy: RoutingPolicy,
        transport: Optional[Callable[..., TransportResult]] = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        rng: Optional[random.Random] = None,
        ledger: Any = None,
        wake_id: str = "routing",
    ):
        self.policy = policy
        self.transport = transport or urllib_transport
        self.sleep = sleep
        self.clock = clock
        self.rng = rng or random.Random()
        self.ledger = ledger
        self.wake_id = wake_id

    # ── ledger hook (optional, never required) ────────────────────────
    def _ledger_log(self, kind: str, detail: Dict[str, Any],
                    model: Optional[str] = None, outcome: str = "ok") -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.log(self.wake_id, kind, json.dumps(detail),
                            source="routing", model=model, outcome=outcome)
        except Exception:
            pass  # telemetry must never take down routing

    def _backoff(self, tier: TierPolicy, try_number: int) -> float:
        base = tier.backoff_base_s * (2 ** (try_number - 1))
        return base + self.rng.uniform(0, base / 2)

    # ── main entry ────────────────────────────────────────────────────
    def complete(
        self,
        tier_name: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        min_content_chars: Optional[int] = None,
        check_refusal: bool = False,
        **payload_kw: Any,
    ) -> RoutedResult:
        tier = self.policy.tier(tier_name)
        candidates = self.policy.candidates_for(tier_name)
        attempts: List[AttemptRecord] = []
        failover_events: List[Dict[str, Any]] = []
        min_chars = max(1, min_content_chars
                        if min_content_chars is not None
                        else tier.min_content_chars)

        for idx, cand in enumerate(candidates):
            next_cand = candidates[idx + 1] if idx + 1 < len(candidates) else None
            retries_allowed = tier.max_retries_same
            try_number = 0
            while True:
                try_number += 1
                payload = {
                    "model": cand.model,
                    "messages": messages,
                    "max_tokens": cand.max_tokens,
                    **payload_kw,
                }
                if tools:
                    payload["tools"] = tools
                started = self.clock()
                try:
                    result = self.transport(cand, payload, cand.timeout_s)
                except TransportError as e:
                    cls = classify_error(e.status, e.body)
                    if cls.max_same_retries is not None:
                        retries_allowed = min(retries_allowed,
                                              cls.max_same_retries)
                    rec = AttemptRecord(
                        candidate=cand.id, provider=cand.provider,
                        model=cand.model, index=idx, try_number=try_number,
                        ts=started, outcome="transport_error",
                        reason=cls.reason, decision=cls.decision.value,
                        detail=cls.detail or str(e),
                        latency_s=self.clock() - started,
                    )
                    attempts.append(rec)
                    if (cls.decision is Decision.RETRY_SAME
                            and try_number <= retries_allowed):
                        self.sleep(self._backoff(tier, try_number))
                        continue
                    # failover (or retry budget spent)
                    event = {
                        "from": cand.id,
                        "to": next_cand.id if next_cand else None,
                        "reason": cls.reason,
                        "detail": cls.detail or str(e),
                        "kind": "transport_error",
                    }
                    failover_events.append(event)
                    self._ledger_log("failover", event, model=cand.id,
                                     outcome="error")
                    break  # → next candidate

                # 2xx: parse and verify the contract
                content, tool_calls, finish_reason = _parse_choice(result.body)
                contract = verify_response(
                    content, tool_calls, finish_reason,
                    tools_expected=bool(tools),
                    min_content_chars=min_chars,
                    raw_body=result.body,
                    check_refusal=check_refusal,
                )
                if contract.ok:
                    attempts.append(AttemptRecord(
                        candidate=cand.id, provider=cand.provider,
                        model=cand.model, index=idx, try_number=try_number,
                        ts=started, outcome="ok", reason=Reason.OK.value,
                        latency_s=self.clock() - started,
                    ))
                    degraded = bool(failover_events) or cand.id != candidates[0].id
                    if degraded:
                        self._ledger_log(
                            "routing_degraded",
                            {"served_by": cand.id, "tier": tier_name,
                             "failovers": len(failover_events)},
                            model=cand.id, outcome="ok",
                        )
                    return RoutedResult(
                        content=content or "",
                        tool_calls=tool_calls,
                        model_used=cand.model,
                        provider=cand.provider,
                        attempts=attempts,
                        degraded=degraded,
                        failover_events=failover_events,
                        raw_body=result.body,
                    )

                # contract failure: NEVER a success, NEVER retried same-model
                rec = AttemptRecord(
                    candidate=cand.id, provider=cand.provider,
                    model=cand.model, index=idx, try_number=try_number,
                    ts=started, outcome="contract_failed",
                    reason=contract.reason.value,
                    decision=Decision.FAILOVER_NEXT.value,
                    detail=contract.detail,
                    latency_s=self.clock() - started,
                )
                attempts.append(rec)
                event = {
                    "from": cand.id,
                    "to": next_cand.id if next_cand else None,
                    "reason": contract.reason.value,
                    "detail": contract.detail,
                    "kind": "contract_failure",
                }
                failover_events.append(event)
                self._ledger_log("failover", event, model=cand.id,
                                 outcome="error")
                break  # → next candidate

        # chain exhausted
        self._ledger_log(
            "routing_exhausted",
            {"tier": tier_name,
             "attempts": [a.to_dict() for a in attempts]},
            outcome="error",
        )
        raise RoutingExhausted(tier_name, attempts)
