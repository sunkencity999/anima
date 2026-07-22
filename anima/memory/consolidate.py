"""Consolidation pass — the "background organ" (ARCHITECTURE.md §2).

Drains consolidation_queue and, per candidate, decides one of:
    confirm     — matches an existing belief → refresh it (provenance,
                  last_confirmed, confidence bump)
    contradict  — negates an existing belief → mark it contradicted and
                  promote the candidate as its successor
    promote     — genuinely new → add as a fresh belief
    reject      — noise, not worth a belief

Two engines:
- **LLM mode** (default when not --dry-run): calls a local
  OpenAI-compatible chat endpoint (ANIMA_CONSOLIDATE_ENDPOINT, default
  http://127.0.0.1:8103/v1; model from ANIMA_CONSOLIDATE_MODEL) and asks
  for a JSON verdict per candidate. Falls back to the heuristic on any
  endpoint/parse failure — consolidation must never wedge the queue.
- **Heuristic mode** (dry_run=True): pure-stdlib token-overlap matching so
  tests and offline boxes work without a model.

Heuristic rules (design decision):
    similarity = Jaccard overlap of lowercased word sets (stopwords dropped)
    - sim >= 0.75 and no negation flip → confirm existing belief
    - sim >= 0.5 and negation markers differ → contradict + supersede
    - candidate < 3 content words → reject (too thin to be a belief)
    - otherwise → promote (confidence 0.6)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

from .store import MemoryStore

DEFAULT_ENDPOINT = os.environ.get(
    "ANIMA_CONSOLIDATE_ENDPOINT", "http://127.0.0.1:8103/v1"
)
DEFAULT_MODEL = os.environ.get("ANIMA_CONSOLIDATE_MODEL", "local")

_STOPWORDS = frozenset(
    "a an the is are was were be been being to of in on at for with and or "
    "that this it its as by from we i you he she they".split()
)
_NEGATION = frozenset(
    ["not", "no", "never", "dead", "removed", "deprecated", "longer",
     "stopped", "broken", "disabled", "gone"]
)
_WORD = re.compile(r"[a-z0-9_]+")


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _heuristic_verdict(store: MemoryStore, candidate: dict) -> dict:
    """Pure-stdlib verdict: {action, belief_id?, reason}."""
    text = candidate["candidate"]
    cwords = _content_words(text)
    if len(cwords) < 3:
        return {"action": "reject", "reason": "too thin to be a belief"}

    best_sim, best = 0.0, None
    for belief in store.search_beliefs(text, limit=10):
        if belief["status"] == "contradicted":
            continue
        sim = _jaccard(cwords, _content_words(belief["statement"]))
        if sim > best_sim:
            best_sim, best = sim, belief

    if best is not None:
        cand_neg = bool(cwords & _NEGATION)
        belief_neg = bool(_content_words(best["statement"]) & _NEGATION)
        neg_flip = cand_neg != belief_neg
        if best_sim >= 0.75 and not neg_flip:
            return {"action": "confirm", "belief_id": best["id"],
                    "reason": f"matches belief {best['id']} (sim {best_sim:.2f})"}
        if best_sim >= 0.5 and neg_flip:
            return {"action": "contradict", "belief_id": best["id"],
                    "reason": f"negates belief {best['id']} (sim {best_sim:.2f})"}
    return {"action": "promote", "reason": "novel statement"}


_LLM_SYSTEM = """You maintain an agent's semantic memory. Given a candidate \
learning and a list of existing beliefs, reply with ONLY a JSON object:
{"action": "confirm"|"contradict"|"promote"|"reject", "belief_id": <int or null>, "reason": "<short>"}
- confirm: candidate restates an existing belief (give its id)
- contradict: candidate invalidates an existing belief (give its id)
- promote: candidate is a new durable fact worth remembering
- reject: candidate is noise, transient, or not a factual belief"""


def _llm_verdict(store: MemoryStore, candidate: dict,
                 endpoint: str, model: str, timeout: float = 60.0) -> Optional[dict]:
    """Ask the local model for a verdict; None on any failure."""
    beliefs = store.search_beliefs(candidate["candidate"], limit=8)
    belief_lines = [
        {"id": b["id"], "statement": b["statement"], "status": b["status"]}
        for b in beliefs
    ]
    payload = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": json.dumps({
                "candidate": candidate["candidate"],
                "existing_beliefs": belief_lines,
            }, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        content = body["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        verdict = json.loads(match.group(0))
        if verdict.get("action") not in {"confirm", "contradict", "promote", "reject"}:
            return None
        known_ids = {b["id"] for b in beliefs}
        if verdict["action"] in {"confirm", "contradict"} and \
                verdict.get("belief_id") not in known_ids:
            return None  # model hallucinated an id — fall back
        return verdict
    except (urllib.error.URLError, OSError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _apply_verdict(store: MemoryStore, candidate: dict, verdict: dict) -> str:
    action = verdict["action"]
    eids = candidate["episode_ids"]
    if action == "confirm":
        store.confirm_belief(verdict["belief_id"], episode_ids=eids)
        resolution = f"confirmed belief {verdict['belief_id']}: {verdict.get('reason', '')}"
        store.resolve_candidate(candidate["id"], resolution)
    elif action == "contradict":
        new_id = store.add_belief(candidate["candidate"], provenance=eids,
                                  confidence=0.7)
        store.contradict_belief(verdict["belief_id"], superseded_by=new_id)
        resolution = (f"contradicted belief {verdict['belief_id']}, "
                      f"superseded by {new_id}: {verdict.get('reason', '')}")
        store.resolve_candidate(candidate["id"], resolution)
    elif action == "promote":
        new_id = store.add_belief(candidate["candidate"], provenance=eids,
                                  confidence=0.6)
        resolution = f"promoted as belief {new_id}: {verdict.get('reason', '')}"
        store.resolve_candidate(candidate["id"], resolution)
    else:  # reject
        resolution = f"rejected: {verdict.get('reason', '')}"
        store.resolve_candidate(candidate["id"], resolution, status="rejected")
    return resolution


def run_consolidation(
    store: MemoryStore,
    dry_run: bool = False,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    limit: int = 50,
    stale_after_days: Optional[float] = None,
) -> dict:
    """Drain up to `limit` pending candidates. Returns a report dict.

    dry_run=True uses the pure heuristic engine (no network at all).
    Optionally also runs staleness decay when stale_after_days is given.
    """
    report = {"processed": 0, "actions": [], "flagged_stale": 0, "engine":
              "heuristic" if dry_run else "llm+heuristic-fallback"}

    for candidate in store.pending_candidates(limit=limit):
        verdict = None
        if not dry_run:
            verdict = _llm_verdict(store, candidate, endpoint, model)
        if verdict is None:
            verdict = _heuristic_verdict(store, candidate)
        resolution = _apply_verdict(store, candidate, verdict)
        report["processed"] += 1
        report["actions"].append({
            "candidate_id": candidate["id"],
            "candidate": candidate["candidate"],
            "action": verdict["action"],
            "resolution": resolution,
        })

    if stale_after_days is not None:
        report["flagged_stale"] = len(store.flag_stale_beliefs(stale_after_days))

    return report
