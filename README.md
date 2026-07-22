# ANIMA

Pure-stdlib implementation of [ARCHITECTURE.md](ARCHITECTURE.md) — no pip
dependencies (pytest only for the test suite).

- **Phase 1 — memory engine** (Build Order #1): three-layer
  SQLite+FTS5 entity memory substrate.
- **Phase 2 — wake scheduler** (Build Order #2): message/timer/drive/
  sense wake sources, priority dispatch, enforced settle, action ledger.
- **Phase 3 — routing contract layer** (Build Order #3): declarative
  model routing with failover as a *verified contract*. Standalone —
  importable without the memory/wake packages.

## Layout

```
anima/memory/
├── store.py        # MemoryStore: episodic / semantic / procedural + consolidation_queue
├── recall.py       # hybrid recall (FTS5 keyword × recency half-life × actor/tag filters)
├── settle.py       # settle-phase writer: wake report → episodes + belief candidates
├── consolidate.py  # consolidation organ: LLM (local endpoint) or heuristic dry-run
└── cli.py          # python3 -m anima.memory ...
anima/wake/
├── sources.py      # Wake + MessageSource / TimerSource / DriveSource / SenseSource
├── scheduler.py    # WakeScheduler: priority queue, coalescing, guarded dispatch
├── orient.py       # orient-phase context pack (recall + intentions + drive pressure)
├── settle_guard.py # ENFORCED settle: handlers cannot skip settlement
└── ledger.py       # append-only action ledger (§6) + audit stats
anima/routing/
├── policy.py       # capability tiers + ordered candidate chains + prefer_local_when
├── contract.py     # response contract verifier (empty reply = ALWAYS a failure)
├── classify.py     # provider error → retry_same / failover_next decision
├── router.py       # chain walker: transport → classify/contract → audited result
└── shim.py         # python3 -m anima.routing probe ... (CLI + harness-shim doc)
examples/policy.example.json  # realistic chain: azure → azure → local 8103 → ollama
tests/              # pytest suite (python3 -m pytest)
demo.py             # deterministic Phase 2 simulation (python3 demo.py)
```

# Phase 1: Memory Engine

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

# Phase 2: Wake Scheduler

The agent is a process that *wakes* (§1) — for a message, a timer, a
drive crossing threshold, or a sense event. Heartbeats disappear as a
concept. Run the deterministic simulation:

```bash
cd projects/anima && python3 demo.py
```

## Wake sources

- **MessageSource** — injectable queue; chat adapters push, scheduler
  polls. Priority 0 (highest).
- **SenseSource** — generic external events (`emit(kind, payload,
  urgent=…)`). Urgent senses rank just below messages; ambient senses
  rank below drives.
- **TimerSource** — one-shot `at()` and recurring `every()` intentions,
  **persisted in `<entity_root>/wake/wake.sqlite`** so scheduled
  intentions survive restart. Sleeping through N periods of a recurring
  timer yields ONE catch-up wake, not N. `open_intentions()` feeds the
  orient pack.
- **DriveSource** (§4) — drives from a `drives.yaml`-style dict
  (`rate_per_hour`, `threshold`, `budget`, `description`). Pressure
  accumulates lazily at poll time; crossing threshold emits ONE budgeted
  wake (`max_tokens` / `max_actions` / `risk_cap`) and latches until
  `satisfy()` resets pressure — motivation with a leash budget, no
  re-fire spam. State persists in sqlite.

## Scheduler

`WakeScheduler(store, handler, sources=…, ledger=…, clock=…)`:

- Priority queue: **message > urgent sense > timer > drive > ambient
  sense**, FIFO within a class.
- **Coalescing:** pending wakes with the same `(source, key)` merge into
  the earliest one (`payload["coalesced"]` keeps the merged tail; the
  merged wake escalates to the most urgent priority involved).
- The **only** dispatch path runs through the settle guard.

## Enforced settle

`settle_guard.run_settled()` (and the `SettleGuard` context manager)
make settlement architecturally impossible to skip: a handler that
returns a wake-report dict settles it verbatim; a handler that returns
`None`/garbage settles a synthesized "completed without report"
episode; a handler that **raises** settles a failure episode with the
full traceback — and only then may the exception propagate. Memory gets
the record either way. No more mental notes.

## Orient

`orient(store, wake, now=…, timer_source=…, drive_source=…)` rebuilds
working memory for a wake: trigger + budget, hybrid memory recall keyed
on the wake reason/payload, open intentions, and a drive-pressure bar
chart. Returns markdown ready for prompt injection.

## Ledger (§6)

`Ledger` — append-only `actions` table at
`<entity_root>/ledger/ledger.sqlite`: wake_id, ts, source, kind, detail,
model, tokens in/out, cost, outcome. No update/delete API exists.
`ledger.bind(wake)` gives handlers a pre-bound `log_action()`. `stats()`
rolls up actions per day / per wake source / per kind plus token & cost
totals — honest self-audit for free.

## Phase 2 design decisions beyond spec

- **Injectable clock everywhere.** Sources never call `time.time()`;
  the scheduler owns `now` and passes it down. Tests and the demo are
  fully deterministic — zero sleeps.
- **Drives are born at first sight:** a drive's baseline timestamp is
  set at its first poll, so a freshly configured drive starts at zero
  pressure rather than retroactively accumulating.
- **Partial satisfaction** (`satisfy(name, amount=…)`) reduces pressure;
  if it stays ≥ threshold the latch is kept (no instant re-fire loop).
- **Message/sense queues are ephemeral by design** — redelivery of
  in-flight events is the transport's job; *intentions* (timers) and
  *motivation* (drives) are the durable state.
- Scheduler logs `dispatch` and `settle` ledger entries around every
  wake when a ledger is attached, so auditability is structural even if
  the handler logs nothing.

# Phase 3: Model Routing + Failover Contract (§3)

## Why this layer exists

Three real production bug classes (each cost a hand-applied dist patch on a
live harness) are made *structurally impossible* here:

1. **Empty reply marked success.** A harness classified empty payloads from
   a candidate as success and terminated the fallback chain with nothing to
   show. Here, `verify_response()` fails any response with no content and
   no valid tool calls — **unconditionally**. `min_content_chars` is clamped
   to ≥ 1 at both the policy and contract layer; there is no configuration
   that permits an empty reply to pass. It's an invariant, not a setting.
2. **Anthropic-shaped 400 bodies misclassified as retryable.** Error JSON
   like `{"type":"error","error":{"type":"invalid_request_error",...}}` —
   arriving *without* usable HTTP status context — was classified "unknown"
   and retried on the same dead model until terminal failure. Here,
   `classify_error(status, body)` parses OpenAI- and Anthropic-shaped bodies
   (dict, string, even string with log-prefix junk) *before* consulting the
   status code, and `status=None` classifies correctly.
3. **DeploymentNotFound marked candidate_succeeded.** A hard "this model
   does not exist" was logged as success and the chain terminated. Here,
   `DeploymentNotFound` / `model_not_found` / `not_found_error` map to
   `failover_next` with a retry budget of **zero**, and unknown errors
   default to *failover*, never to success and never to unbounded retries.

## Quick start

```bash
# real probe through a chain (prints attempt audit)
python3 -m anima.routing probe --policy examples/policy.example.json \
    --tier standard --prompt "Say hello in five words."
```

```python
from anima.routing import Router, RoutingPolicy, RoutingExhausted

policy = RoutingPolicy.from_file("examples/policy.example.json")
router = Router(policy)                      # optional: ledger=Ledger(root)
try:
    r = router.complete("standard", [{"role": "user", "content": "hi"}])
    if r.degraded:                            # failover is first-class telemetry
        print("served by fallback:", r.model_used, r.failover_events)
except RoutingExhausted as e:
    for a in e.attempts:                      # full audit, always
        print(a.candidate, a.outcome, a.reason)
```

## How a request flows

```
Router.complete(tier, messages)
  └─ for each candidate (policy order, prefer_local_when applied):
       transport call
         ├─ error → classify_error(status, body)
         │           ├─ retry_same    → jittered exp backoff, bounded by
         │           │                  tier budget (auth clamps to 1)
         │           └─ failover_next → next candidate + failover event
         └─ 200   → verify_response(content, tool_calls, finish_reason, body)
                     ├─ ok   → RoutedResult   ← the ONLY chain exit
                     └─ fail → failover_next (contract failures are never
                                retried on the same candidate)
  all candidates spent → RoutingExhausted(attempts=[full audit])
```

## Phase 3 design decisions beyond spec

- **Contract failures never retry the same candidate.** Same prompt + same
  model ≈ same hole; retry budget is reserved for *transient transport*
  errors (429/5xx/timeout). This also bounds worst-case chain latency.
- **Body rules outrank status codes** in the classifier — the status may be
  missing or lying (429 carrying `insufficient_quota` is billing/failover,
  not rate-limit/retry).
- **Unknown → failover_next**, the safe direction: never success, never an
  infinite same-model retry loop, chain keeps moving toward local models
  that are the most likely to be alive.
- **Billing errors fail over, they don't abort** — one provider being out
  of money says nothing about the next candidate. Abort exists only as the
  end-of-chain `RoutingExhausted`.
- **Standalone by construction:** `anima/routing` imports nothing from
  `anima.memory`/`anima.wake` (a subprocess test enforces this). The ledger
  is duck-typed: anything with `.log(wake_id, kind, detail, ...)` works,
  and ledger failures can never take down routing.
- **Everything injectable:** transport, sleep, clock, rng. The whole test
  suite is offline and deterministic; the default urllib transport is only
  exercised by the CLI probe.
- See `anima/routing/shim.py`'s docstring for wrapping an *existing*
  harness's model call path with this layer as a verification shim.
