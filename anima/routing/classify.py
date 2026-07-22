"""Provider error classifier (ARCHITECTURE.md §3).

Maps HTTP status + error body → a failover decision. Handles both
OpenAI-shape and Anthropic-shape error bodies, INCLUDING bodies that
arrive with no usable HTTP status context (the real-world scar: an
Anthropic-shaped 400 JSON body surfaced as a bare string, which a
production harness classified as "unknown" → retried the same dead
model three times → reported terminal failure without ever consulting
the fallback chain; local-patch: 400-fallback, 2026-07-02).

Decisions:
- RETRY_SAME     — transient (429, 5xx, timeouts). Bounded by the tier's
                   retry budget; after budget → failover_next.
- FAILOVER_NEXT  — this candidate is not going to work: 400 invalid
                   request, 404 / DeploymentNotFound, auth (after one
                   retry), billing/quota (that provider is out of money,
                   the next one may not be), contract failures.
- ABORT          — reserved for the router when the whole chain is
                   exhausted. The classifier itself never gives up the
                   chain on a single candidate's error: billing failures
                   on one provider are failover_next, not abort.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Union


class Decision(str, Enum):
    RETRY_SAME = "retry_same"
    FAILOVER_NEXT = "failover_next"
    ABORT = "abort"


@dataclass(frozen=True)
class Classification:
    decision: Decision
    reason: str            # machine-readable: rate_limit, server_error, ...
    detail: str = ""       # human-readable
    max_same_retries: Optional[int] = None  # override tier budget (auth=1)


# error type/code substrings → (decision, reason, max_same_retries)
_BODY_RULES = [
    # hard "this model/deployment does not exist" — NEVER success, never retry
    ("deploymentnotfound", Decision.FAILOVER_NEXT, "not_found", 0),
    ("model_not_found", Decision.FAILOVER_NEXT, "not_found", 0),
    ("not_found_error", Decision.FAILOVER_NEXT, "not_found", 0),
    # schema / request shape problems — same request will fail the same way
    ("invalid_request_error", Decision.FAILOVER_NEXT, "invalid_request", 0),
    ("invalid_request", Decision.FAILOVER_NEXT, "invalid_request", 0),
    ("badrequest", Decision.FAILOVER_NEXT, "invalid_request", 0),
    # billing / quota — that provider is out; the next may not be
    ("insufficient_quota", Decision.FAILOVER_NEXT, "billing", 0),
    ("billing_hard_limit_reached", Decision.FAILOVER_NEXT, "billing", 0),
    ("billing", Decision.FAILOVER_NEXT, "billing", 0),
    ("quota", Decision.FAILOVER_NEXT, "billing", 0),
    # auth — allow exactly one retry (transient token refresh), then move on
    ("authentication_error", Decision.RETRY_SAME, "auth", 1),
    ("permission_error", Decision.RETRY_SAME, "auth", 1),
    ("invalid_api_key", Decision.RETRY_SAME, "auth", 1),
    ("unauthorized", Decision.RETRY_SAME, "auth", 1),
    # rate limiting / overload — transient, retry with backoff
    ("rate_limit", Decision.RETRY_SAME, "rate_limit", None),
    ("overloaded_error", Decision.RETRY_SAME, "overloaded", None),
    ("overloaded", Decision.RETRY_SAME, "overloaded", None),
    ("server_error", Decision.RETRY_SAME, "server_error", None),
    ("api_error", Decision.RETRY_SAME, "server_error", None),
    ("timeout", Decision.RETRY_SAME, "timeout", None),
]


def _extract_error_fields(body: Any) -> Dict[str, str]:
    """Pull type/code/message out of OpenAI- or Anthropic-shaped bodies."""
    out = {"type": "", "code": "", "message": ""}
    if not isinstance(body, dict):
        return out
    err = body.get("error")
    if isinstance(err, dict):
        out["type"] = str(err.get("type") or "")
        out["code"] = str(err.get("code") or "")
        out["message"] = str(err.get("message") or "")
    elif isinstance(err, str):
        out["message"] = err
    # Anthropic top-level {"type": "error", "error": {...}} handled above via
    # the nested error dict; also catch bare {"type": "...error", "message"}.
    if not any(out.values()):
        t = str(body.get("type") or "")
        if "error" in t.lower():
            out["type"] = t
            out["message"] = str(body.get("message") or "")
    return out


def classify_error(
    status: Optional[int],
    body: Union[None, str, Dict[str, Any]] = None,
) -> Classification:
    """Classify a provider failure into a failover decision.

    ``status`` may be None: raw error bodies without HTTP status context
    MUST still classify correctly (that gap caused the 2026-07-02 outage).
    """
    # normalize body → dict if possible
    parsed: Optional[Dict[str, Any]] = None
    raw_text = ""
    if isinstance(body, dict):
        parsed = body
    elif isinstance(body, str):
        raw_text = body
        s = body.strip()
        # tolerate leading junk before the JSON (log-line prefixes etc.)
        idx = s.find("{")
        if idx >= 0:
            try:
                parsed = json.loads(s[idx:])
            except (json.JSONDecodeError, ValueError):
                parsed = None

    fields = _extract_error_fields(parsed) if parsed else {"type": "", "code": "", "message": ""}
    haystack = " ".join(
        [fields["type"], fields["code"], fields["message"], raw_text]
    ).lower()

    # 1. Body-content rules first: they are more specific than status codes
    #    and must win even when status is None or misleading.
    if haystack.strip():
        for needle, decision, reason, budget in _BODY_RULES:
            if needle in haystack:
                detail = (fields["message"] or raw_text)[:300]
                return Classification(decision, reason, detail,
                                      max_same_retries=budget)

    # 2. Status-code rules.
    if status is not None:
        if status == 429:
            return Classification(Decision.RETRY_SAME, "rate_limit",
                                  f"http {status}")
        if status in (500, 502, 503, 504, 520, 522, 524):
            return Classification(Decision.RETRY_SAME, "server_error",
                                  f"http {status}")
        if status in (401, 403):
            return Classification(Decision.RETRY_SAME, "auth",
                                  f"http {status}", max_same_retries=1)
        if status == 404:
            return Classification(Decision.FAILOVER_NEXT, "not_found",
                                  f"http {status}")
        if status == 400:
            return Classification(Decision.FAILOVER_NEXT, "invalid_request",
                                  f"http {status}")
        if status == 402:
            return Classification(Decision.FAILOVER_NEXT, "billing",
                                  f"http {status}")
        if 400 <= status < 500:
            return Classification(Decision.FAILOVER_NEXT, "client_error",
                                  f"http {status}")
        if status >= 500:
            return Classification(Decision.RETRY_SAME, "server_error",
                                  f"http {status}")

    # 3. Unknown errors: the safe default is FAILOVER, never "success" and
    #    never unbounded same-model retries. (A production harness once
    #    mapped unknown → candidate_succeeded. Never again.)
    return Classification(Decision.FAILOVER_NEXT, "unknown",
                          (raw_text or str(body))[:300])
