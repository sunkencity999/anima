# ANIMA — Phase 1: Memory Engine

The keystone from [ARCHITECTURE.md](ARCHITECTURE.md) Build Order #1: a
three-layer, SQLite+FTS5-backed entity memory substrate. Pure stdlib —
no pip dependencies (pytest only for the test suite).

## Layout

```
anima/memory/
├── store.py        # MemoryStore: episodic / semantic / procedural + consolidation_queue
├── recall.py       # hybrid recall (FTS5 keyword × recency half-life × actor/tag filters)
├── settle.py       # settle-phase writer: wake report → episodes + belief candidates
├── consolidate.py  # consolidation organ: LLM (local endpoint) or heuristic dry-run
└── cli.py          # python3 -m anima.memory ...
tests/              # pytest suite (python3 -m pytest)
```

All state lives in `<entity_root>/memory/memory.sqlite`. The entity root
directory IS the agent; the store never writes outside it.

## Quick start

```bash
cd projects/anima

# remember an event
python3 -m anima.memory remember --root ./entity \
    "Fixed the GPU swap guardian" --tags gpu,vision --actors Christopher

# settle a wake (the enforced end-of-wake write)
echo '{"events":["checked manifest job"],
      "learnings":["manifest job needs 2GB heap or it OOMs"],
      "drive_satisfactions":{"stewardship":0.5}}' \
  | python3 -m anima.memory settle --root ./entity

# consolidate learnings → beliefs (heuristic, no model needed)
python3 -m anima.memory consolidate --root ./entity --dry-run

# ...or against the local model (default http://127.0.0.1:8103/v1)
ANIMA_CONSOLIDATE_MODEL=Qwen3-235B python3 -m anima.memory consolidate --root ./entity

# recall → markdown context pack for prompt injection
python3 -m anima.memory recall --root ./entity "manifest job heap" --budget 1000

python3 -m anima.memory stats --root ./entity
```

## The three layers

- **Episodic** — append-only experience log (`ts, wake_id, kind, actors,
  summary, detail, tags`), FTS5-indexed. No update/delete API by design.
- **Semantic** — beliefs with provenance (supporting episode ids),
  confidence 0–1, `last_confirmed`, and lifecycle `active → stale →
  contradicted`. `flag_stale_beliefs(days)` implements staleness decay:
  unconfirmed beliefs degrade to "stale, reverify" instead of rotting
  silently. Contradicted beliefs keep a `superseded_by` pointer.
- **Procedural** — skills with telemetry: success/failure counts,
  `last_worked`, accumulated `known_failure_modes`. A skill that starts
  failing carries its own evidence.

## Settle → consolidate flow

`settle()` takes a structured wake report (events / decisions / learnings /
drive_satisfactions), writes everything episodically under one `wake_id`,
and queues each learning into `consolidation_queue` with episode
provenance. `run_consolidation()` drains the queue: each candidate is
**confirmed** against an existing belief (provenance merged, confidence
bumped), **contradicts** one (old belief superseded), gets **promoted**
as a new belief, or is **rejected** as noise. LLM mode asks a local
OpenAI-compatible endpoint and falls back to the token-overlap heuristic
on any failure — the queue can never wedge on a dead model.

## Tests

```bash
cd projects/anima && python3 -m pytest
```

## Design decisions beyond spec

- Epoch-float timestamps as truth; ISO strings only at render time.
- FTS5 queries are sanitized into OR-of-bare-tokens (`fts_sanitize`) so
  arbitrary natural-language queries can't crash the MATCH parser.
- Recall ranking: `0.7 × normalized-bm25 + 0.3 × recency`, where recency
  is a true half-life decay (default 14 days). Token budget enforced with
  the standard `len//4` heuristic.
- Heuristic consolidation uses Jaccard overlap of stopword-stripped
  content words with a negation-flip detector for contradiction.
- LLM verdicts are validated (action whitelist, belief_id must be one the
  model was shown) before being applied; anything suspect falls back to
  the heuristic.
- `confirm_belief()` on a stale belief revives it to active — confirming
  is the reverification act.
