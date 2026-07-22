"""The response contract verifier (ARCHITECTURE.md §3).

A candidate response PASSES only if every check holds:

- content is non-empty after stripping whitespace (or valid tool_calls
  are present when tools were in play);
- if tool calls were made/expected, each parses: has a function name and
  JSON-valid arguments;
- finish reason is acceptable (not content_filter / error);
- the 200 body is not a provider error payload in disguise (error-shaped
  JSON, Anthropic ``{"type":"error",...}`` or OpenAI ``{"error":{...}}``);
- optional min_content_chars bar is met.

CRITICAL INVARIANT — encoded in code, not config: **an empty reply is
ALWAYS a contract failure.** There is no parameter that can make an empty,
tool-call-free response pass. This exists because a production harness once
classified empty replies as success and terminated its fallback chain on
them (local-patch: empty-response-fallback, 2026-07-19).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Reason(str, Enum):
    OK = "ok"
    EMPTY_REPLY = "empty_reply"
    ERROR_PAYLOAD = "error_payload"
    MALFORMED_TOOL_CALL = "malformed_tool_call"
    CONTENT_FILTER = "content_filter"
    REFUSAL_SHAPED = "refusal_shaped"
    TOO_SHORT = "too_short"


@dataclass(frozen=True)
class ContractResult:
    ok: bool
    reason: Reason
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


# finish reasons that mean the provider did not deliver a usable turn
_BAD_FINISH = {"content_filter", "error", "content-filter", "safety"}

# lightweight, OFF by default: canned-refusal openers
_REFUSAL_RE = re.compile(
    r"^\s*(i('m| am) sorry,? (but )?i (can(no|')t|am unable)"
    r"|i can(no|')t (help|assist) with)"
    , re.IGNORECASE,
)


def _error_shaped(obj: Any) -> Optional[str]:
    """Return a description if obj looks like a provider error payload."""
    if not isinstance(obj, dict):
        return None
    # Anthropic shape: {"type": "error", "error": {"type": ..., "message": ...}}
    if obj.get("type") == "error":
        err = obj.get("error") or {}
        return f"anthropic error payload: {err.get('type')}: {err.get('message')}"
    # OpenAI/Azure shape: {"error": {"code"/"type"/"message": ...}}
    err = obj.get("error")
    if isinstance(err, dict) and ("message" in err or "code" in err or "type" in err):
        return (f"openai error payload: {err.get('code') or err.get('type')}: "
                f"{err.get('message')}")
    if isinstance(err, str) and err.strip():
        return f"error payload: {err}"
    return None


def _check_tool_calls(tool_calls: List[Dict[str, Any]]) -> Optional[str]:
    """Return a failure detail if any tool call is malformed, else None."""
    for i, tc in enumerate(tool_calls):
        if not isinstance(tc, dict):
            return f"tool_call[{i}] is not an object"
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name")
        if not name or not str(name).strip():
            return f"tool_call[{i}] missing function name"
        args = fn.get("arguments", tc.get("arguments"))
        if args is None:
            return f"tool_call[{i}] ({name}) missing arguments"
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                return f"tool_call[{i}] ({name}) arguments are not valid JSON"
            if not isinstance(parsed, dict):
                return f"tool_call[{i}] ({name}) arguments are not a JSON object"
        elif not isinstance(args, dict):
            return f"tool_call[{i}] ({name}) arguments are not an object"
    return None


def verify_response(
    content: Optional[str],
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    finish_reason: Optional[str] = None,
    *,
    tools_expected: bool = False,
    min_content_chars: int = 1,
    raw_body: Optional[Dict[str, Any]] = None,
    check_refusal: bool = False,
) -> ContractResult:
    """Verify a candidate response against the contract.

    Returns ContractResult(ok, reason, detail). The chain may terminate
    ONLY on ok=True.
    """
    # 1. Provider error disguised as a 200 body.
    if raw_body is not None:
        desc = _error_shaped(raw_body)
        if desc:
            return ContractResult(False, Reason.ERROR_PAYLOAD, desc)

    # 2. Bad finish reasons.
    if finish_reason and finish_reason.lower() in _BAD_FINISH:
        return ContractResult(
            False,
            Reason.CONTENT_FILTER if "filter" in finish_reason.lower()
            or finish_reason.lower() == "safety" else Reason.ERROR_PAYLOAD,
            f"finish_reason={finish_reason}",
        )

    # 3. Tool calls, when present, must parse.
    if tool_calls:
        detail = _check_tool_calls(tool_calls)
        if detail:
            return ContractResult(False, Reason.MALFORMED_TOOL_CALL, detail)

    stripped = (content or "").strip()

    # 4. THE INVARIANT: empty reply with no valid tool calls is ALWAYS a
    #    failure. min_content_chars is clamped so it can never permit "".
    effective_min = max(1, int(min_content_chars))
    assert effective_min >= 1, "empty replies can never pass the contract"
    if not stripped and not tool_calls:
        return ContractResult(
            False, Reason.EMPTY_REPLY,
            "empty content and no tool calls — never a success",
        )

    # 5. Content that parses as an error-shaped JSON blob.
    if stripped and stripped[0] in "{[":
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        desc = _error_shaped(parsed)
        if desc:
            return ContractResult(
                False, Reason.ERROR_PAYLOAD, f"error JSON in content: {desc}")

    # 6. Minimum-length bar (only when there is content to measure and no
    #    tool calls carrying the payload).
    if stripped and not tool_calls and len(stripped) < effective_min:
        return ContractResult(
            False, Reason.TOO_SHORT,
            f"content {len(stripped)} chars < min {effective_min}",
        )

    # 7. If tools were expected but the model produced neither tool calls
    #    nor content, that was caught above; tools_expected with content
    #    only is allowed (models may answer directly).

    # 8. Optional refusal heuristic (opt-in; can false-positive).
    if check_refusal and stripped and _REFUSAL_RE.match(stripped):
        return ContractResult(
            False, Reason.REFUSAL_SHAPED, "canned-refusal opener detected")

    return ContractResult(True, Reason.OK, "")
