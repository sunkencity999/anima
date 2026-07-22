"""Declarative routing policy (ARCHITECTURE.md §3).

Routing is a policy document, not a hardcoded chain. Capability tiers
(reflex / standard / deep / verified_code / anything you name) each carry
an ordered candidate list. Local models are peers, not afterthoughts:
`prefer_local_when` expresses "prefer local candidates for these tiers"
natively, so a 96% local-offload ratio is policy, not luck.

Load from a dict or a JSON file:

    policy = RoutingPolicy.from_file("policy.json")
    for cand in policy.candidates_for("standard"):
        ...

Policy document shape (JSON)::

    {
      "prefer_local_when": {"tiers": ["reflex", "standard"]},
      "defaults": {"max_retries_same": 2, "backoff_base_s": 0.5},
      "tiers": {
        "standard": {
          "min_content_chars": 1,
          "candidates": [
            {"provider": "azure-anthropic", "model": "claude-x",
             "base_url": "https://...", "api_key_env": "AZURE_KEY",
             "max_tokens": 4096, "timeout_s": 120,
             "cost_tier": "frontier", "local": false},
            ...
          ]
        }
      }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class PolicyError(ValueError):
    """Raised for malformed or incomplete policy documents."""


@dataclass(frozen=True)
class Candidate:
    """One model endpoint in a tier's ordered chain."""

    provider: str
    model: str
    base_url: str
    api_key_env: Optional[str] = None
    max_tokens: int = 4096
    timeout_s: float = 120.0
    cost_tier: str = "standard"
    local: bool = False

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Candidate":
        try:
            return cls(
                provider=d["provider"],
                model=d["model"],
                base_url=d["base_url"],
                api_key_env=d.get("api_key_env"),
                max_tokens=int(d.get("max_tokens", 4096)),
                timeout_s=float(d.get("timeout_s", 120.0)),
                cost_tier=d.get("cost_tier", "standard"),
                local=bool(d.get("local", False)),
            )
        except KeyError as e:  # pragma: no cover - message clarity
            raise PolicyError(f"candidate missing required field {e}") from e


@dataclass
class TierPolicy:
    """Ordered candidate chain + per-tier knobs."""

    name: str
    candidates: List[Candidate]
    min_content_chars: int = 1
    max_retries_same: int = 2
    backoff_base_s: float = 0.5
    expect_tool_support: bool = False

    def __post_init__(self) -> None:
        if not self.candidates:
            raise PolicyError(f"tier {self.name!r} has no candidates")
        # Empty replies are ALWAYS contract failures; min_content_chars can
        # raise the bar but never lower it below 1. (Invariant, not config.)
        if self.min_content_chars < 1:
            self.min_content_chars = 1


class RoutingPolicy:
    """The full routing policy: tiers + local-preference rule."""

    def __init__(
        self,
        tiers: Dict[str, TierPolicy],
        prefer_local_when: Optional[Dict[str, Any]] = None,
    ):
        if not tiers:
            raise PolicyError("policy has no tiers")
        self.tiers = tiers
        self.prefer_local_when = prefer_local_when or {}

    # ── construction ──────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, doc: Dict[str, Any]) -> "RoutingPolicy":
        defaults = doc.get("defaults", {})
        tiers_doc = doc.get("tiers")
        if not isinstance(tiers_doc, dict) or not tiers_doc:
            raise PolicyError("policy document needs a non-empty 'tiers' map")
        tiers: Dict[str, TierPolicy] = {}
        for name, td in tiers_doc.items():
            cands = [Candidate.from_dict(c) for c in td.get("candidates", [])]
            tiers[name] = TierPolicy(
                name=name,
                candidates=cands,
                min_content_chars=int(
                    td.get("min_content_chars",
                           defaults.get("min_content_chars", 1))),
                max_retries_same=int(
                    td.get("max_retries_same",
                           defaults.get("max_retries_same", 2))),
                backoff_base_s=float(
                    td.get("backoff_base_s",
                           defaults.get("backoff_base_s", 0.5))),
                expect_tool_support=bool(
                    td.get("expect_tool_support", False)),
            )
        return cls(tiers, prefer_local_when=doc.get("prefer_local_when"))

    @classmethod
    def from_file(cls, path: str) -> "RoutingPolicy":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    # ── queries ───────────────────────────────────────────────────────
    def tier(self, name: str) -> TierPolicy:
        try:
            return self.tiers[name]
        except KeyError:
            raise PolicyError(
                f"unknown tier {name!r}; known: {sorted(self.tiers)}"
            ) from None

    def _prefers_local(self, tier_name: str) -> bool:
        rule = self.prefer_local_when
        if not rule:
            return False
        if rule.get("always"):
            return True
        return tier_name in rule.get("tiers", [])

    def candidates_for(self, tier_name: str) -> List[Candidate]:
        """Ordered candidates for a tier, with the prefer_local_when rule
        applied: when the rule matches, local candidates are stably moved
        to the front (original relative order preserved on both sides)."""
        tier = self.tier(tier_name)
        cands = list(tier.candidates)
        if self._prefers_local(tier_name):
            local = [c for c in cands if c.local]
            remote = [c for c in cands if not c.local]
            cands = local + remote
        return cands
