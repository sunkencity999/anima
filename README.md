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
- **Phase 4 — relationship ACLs + entity root** (Build Order #4):
  per-person relationship records with *structural* privacy walls
  (scoped memory + SQL-compiled access control), and `EntityRoot`,
  which assembles the whole organism from a directory.
- **Phase 5 — runtime shell** (Build Order #5): the process. Risk-tiered
  tool registry (the entity's hands), the agent turn (act phase), a
  wall-clock shell with pidfile single-writer discipline and graceful
  shutdown-as-settle, and senses (console / HTTP / OpenClaw bridge).
- **Phase 6a — packaging + CLI + Telegram sense**: installable
  `anima-harness` package with the `anima` entity-lifecycle CLI
  (init / run / status / sync-as-migration) and a pure-stdlib Telegram
  long-polling sense.

## Phase 6a: install + CLI + Telegram

```bash
pip install -e .            # console script: anima

anima init ~/entities/me    # scaffold an entity root (refuses to
                            # overwrite existing identity files)
anima status --root ~/entities/me   # memory / drives / lineage / lock
anima run --root ~/entities/me --console
anima sync ~/entities/me /mnt/newhome/me   # MIGRATION, not cloning:
                            # refuses while the runtime lock is live,
                            # records a migration lineage entry on the
                            # source BEFORE copying (both forks carry
                            # it), and warns that forks diverge.
```

**Telegram sense** (`anima run --root R --telegram`): pure-stdlib Bot
API long polling — no SDK. Configure `R/senses/telegram.json`:

```json
{
  "token_env": "ANIMA_TELEGRAM_TOKEN",
  "allowed_chat_ids": [123456789],
  "person_map": {"123456789": "christopher"},
  "operator_person": "christopher"
}
```

- Token comes from the env var (or a `"token"` literal for tests).
- The allowlist **fails closed**: an empty list means no chats.
- Private chats become `direct` AccessContexts with the mapped person;
  unmapped-but-allowed users get an auto identity `tg-<user_id>`
  upserted into the RelationshipStore. Group chats become `group`
  contexts, so private-scoped memory is structurally invisible there.
- Disallowed chats are ignored *and* noted in the ledger.
- The getUpdates offset persists in `senses/telegram_offset.json`, so
  restarts never replay old messages. Replies go back to the
  originating chat via sendMessage.

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
anima/relationships/
├── context.py      # AccessContext: who is in the room (direct/group/public/system)
├── acl.py          # scope rules compiled to SQL WHERE — the enforcement core
└── model.py        # RelationshipStore: person records, trust, household, mirrors
anima/entity.py     # EntityRoot: the whole organism assembled from a directory
anima/runtime/
├── tools.py        # ToolRegistry: risk tiers, budgets, built-in hands
├── agent_turn.py   # the act phase: orient ⊕ identity → model loop → settle report
├── shell.py        # RuntimeShell: pidfile lock, wall-clock loop, graceful shutdown
├── __main__.py     # python3 -m anima.runtime --root <entity_root>
└── senses/         # console.py, http_sense.py, openclaw_bridge.md
examples/policy.example.json  # realistic chain: azure → azure → local 8103 → ollama
tests/              # pytest suite (python3 -m pytest)
demo.py             # deterministic Phase 2 simulation (python3 demo.py)
demo_runtime.py     # Phase 5 acceptance rehearsal: continuity across death
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

# Phase 4: Relationship ACLs + Entity Root (§5)

The Esmeralda/Antonia lesson: per-person privacy must be **structural,
not disciplinary**. Phase 4 makes recall from a shared context
*physically unable* to return private-scoped memories.

## Scopes and contexts

Every memory row (episodic, semantic, procedural) carries a `scope`
(`private | household | shared | public`, default `shared`) and an
optional `owner_person_id`. Every ACL-enforced read carries an
`AccessContext` — `{context_id, kind: direct|group|public|system,
participants, channel}` (constructors: `AccessContext.direct(person)`,
`.group([...])`, `.public()`, `.system()`).

Visibility (whitelist; **deny by default** — a row with an unknown
scope is dark to every person context):

| scope     | visible when |
|-----------|--------------|
| private   | `kind == direct` AND owner ∈ participants |
| household | ≥1 participant AND every participant is a household member |
| shared    | any authenticated context (direct/group/system) |
| public    | anywhere |

`system` contexts (the entity's own organs: consolidation, self-audit)
see everything — the wall is between *people*, not between the entity
and its own mind.

## Enforcement is IN the SQL

`compile_acl(context, household_members)` produces a `CompiledACL`
whose `.where(prefix)` emits a parameterized WHERE fragment; the store
splices it into the FTS/scan queries themselves. Unauthorized rows are
excluded by sqlite and **never cross into Python** — no ranking bug,
rendering bug, or bad model day can leak them. The test suite proves
it with sqlite trace callbacks and raw-cursor replays.

```python
from anima.memory.recall import build_context_pack
from anima.relationships import AccessContext

pack = build_context_pack(store, "party budget",
                          access_context=AccessContext.group(["a", "b"]),
                          relationships=rel_store)
```

`access_context=None` keeps exact pre-Phase-4 behavior (single-user
mode) and emits a one-time `UserWarning`.

## RelationshipStore

Per-person records in `relationships/relationships.sqlite` (person_id
is the join key): profile (name, aliases, channel handles, notes),
trust tier (`stranger…inner`), a standing ACL declaration
(`{scope, allowed_contexts}` — feeds *write-time* defaults; read-time
enforcement is always the AccessContext path), and a household table.
Every upsert mirrors a human-readable `relationships/<person>/
profile.json` — the directory stays the agent.

## EntityRoot — the directory IS the agent, runnable

```python
from anima.entity import EntityRoot
from anima.relationships import AccessContext

with EntityRoot("./entity") as e:
    e.relationships.upsert_person("antonia")
    e.wake_message("antonia", "my secret plan",
                   AccessContext.direct("antonia"))   # → private episode
    e.recall("secret plan", AccessContext.group(["antonia", "chris"]))
    # → structurally absent
```

`EntityRoot(root)` wires MemoryStore + recall + WakeScheduler (message/
timer/sense + drives from `identity/drives.json`) + Ledger +
RelationshipStore + optional Router from `identity/routing.json`, and
maintains `identity/lineage.log` — append-only biography: first init
and every runtime-version change are recorded events. Public surface:
`wake_message(sender, text, context)`, `recall(query, context)`,
`settle(report)`, `stats()`.

## Phase 4 design decisions beyond spec

- **Contextual auto-scoping on write:** the default message handler
  scopes direct-context messages `private/owner=sender` — what someone
  tells you one-on-one is theirs by default; escalation to shared is a
  deliberate act, never a default.
- **Household is granted only when there ARE participants** — `all()`
  over an empty set is vacuously true and would have granted household
  scope to anonymous rooms.
- **The store never imports the relationships package.** ACL objects
  are duck-typed (`.where(prefix)`), so Phase 1 stays standalone and
  the dependency arrow points one way.
- **v1→v2 migration is ALTER TABLE with defaults** (`scope='shared'`),
  so pre-Phase-4 entity roots open unchanged and legacy rows behave
  exactly as before.
- **Write-time scope validation** (`ValueError` on unknown scopes) plus
  read-time deny-by-default: even a corrupted row with `scope='banana'`
  is invisible to every person context.

# Phase 5: Runtime Shell (Build Order #5)

The organism breathes wall-clock air. Design note: `docs/PHASE5_RUNTIME.md`
(written before implementation, followed as the spec).

## Quick start

```bash
# talk to an entity on a laptop with zero infrastructure
python3 -m anima.runtime --root ./entity --console \
    --policy ./entity/identity/routing.json

# webhook mode (senses/http.json must exist inside the root)
python3 -m anima.runtime --root ./entity --http

# the acceptance rehearsal: continuity across death, offline
python3 demo_runtime.py
```

## The act phase (agent turn)

```
wake ──► orient (identity soul.md ⊕ orient pack ⊕ wake payload)
      ──► loop bounded by budget:
            router.complete(tier(wake), messages,
                            tools=registry.schemas(risk_cap))
            tool_calls → registry.execute (ledger row + episodic event)
            final      → parse ```settle block (or synthesize from trail)
      ──► wake report ──► settle guard (Phase 2, enforced)
```

Tier is wake-derived (message/sense→`standard`, drive/timer→`reflex`),
overridable via `wake.payload["tier"]` — policy picks models, not code.

## The tool registry — hands with a leash

| tool          | risk   | notes                                        |
|---------------|--------|----------------------------------------------|
| recall        | low    | carries the WAKE's AccessContext into the ACL |
| remember      | low    | learning/event/decision → settle pipeline     |
| set_timer     | low    | schedules the entity's own future wake        |
| satisfy_drive | low    | closes a drive loop                           |
| reply         | low    | outbound via the wake's originating sense     |
| http_get      | medium | read-only web (injectable transport)          |
| shell         | high   | needs entity opt-in AND risk_cap=high         |

Defense in depth, same as the ACLs: the schema list *offered* to the
model is filtered by the wake's risk_cap, AND execution re-checks — a
hallucinated call to an unoffered tool is denied structurally (denials
are free; only real executions spend `max_actions`). Budget exhaustion
ends the turn with a truthful report, never a fake completion.

## The shell

- **Single-writer:** pidfile lock; a second shell against a live root is
  refused ("two runtimes sharing a self is corruption, not concurrency").
- **Graceful shutdown is a settle event:** SIGTERM/SIGINT → drain →
  settle a "shutdown" episode → lineage entry. The entity always knows
  it went to sleep.
- **Senses** run in their own threads and only *inject*; one dispatch
  lock serializes all wake execution (sqlite stays single-writer).

## Senses

- `console` — stdin/stdout chat, injectable I/O.
- `http_sense` — stdlib http.server, bearer-token auth, loopback bind by
  default. `POST /message` / `POST /event` inject wakes with proper
  AccessContexts; replies go to a `callback_url` or drain via
  `GET /replies`. External callers cannot claim `kind:"system"`.
- `openclaw_bridge.md` — how OpenClaw forwards inbound messages to the
  http sense and relays replies back: OpenClaw as a *peripheral*.

## Phase 5 design decisions beyond spec

- **The orient pack is ACL-walled too** (found during build): `orient()`
  now takes the wake's AccessContext, so a group wake's *prompt* cannot
  leak private rows before the model even acts. The recall tool being
  walled is not enough if the runtime itself leaks in the system prompt.
- **Context defaulting:** a non-message wake without an AccessContext is
  the entity waking itself (drive/timer) and runs as `system` — the
  organism knows its own mind. A message wake missing a context falls
  back to direct-with-sender, never system.
- **Denials don't spend budget:** the model pays for actions, not for
  being told no — otherwise a low-cap wake could be starved into
  uselessness by its own hallucinated high-risk calls.
- **Risk-cap vocabulary fails closed:** wake budgets say
  low/normal/high; `normal` maps to `medium`, anything unrecognized
  maps to `low`.
- **Sense threads never run wakes:** HTTP handlers only inject; the
  shell loop owns dispatch. Keeps settle/ledger ordering coherent and
  sqlite happy (`check_same_thread=False` + one dispatch lock).
- **`json tail` settle fallback:** small local models are bad at fences;
  a bare trailing JSON object also parses as a settle block, and if
  nothing parses the runtime synthesizes the report from the ledger
  trail — the enforced-settle guarantee never depends on model manners.
