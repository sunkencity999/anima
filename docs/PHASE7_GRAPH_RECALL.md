# Phase 7 — Graph Recall: memory as a web, not a list

*Design note, Cherubesque, 2026-08-05. Written before the builder, as with
Phase 5. Inspired by Yohei Nakajima's Active Graph Agent Runtime (BabyAGI 4);
adapted, not adopted — we take the pattern, not the framework.*

## Why

Flat retrieval answers "what is *similar* to this query." A graph answers
"what is *connected* to it." The difference is multi-hop reasoning:
decision → the constraint that drove it → the incident that created the
constraint. For small-context local models, the graph converts context from
a scarce resource you pre-fill into a cache you populate on demand — the
entity boots tiny and pulls what the moment needs.

Second half of the pattern: retrieval as a *delegated* job. The traversal
burns the retriever's context, not the thinker's. The wake that needs the
memory receives only distilled nodes.

If this proves out here, the pattern ports to Lucen (48K ceiling, bootstrap
files growing linearly — the chronic version of the problem this solves).

## What exists already

- sqlite memory stores per entity root, with ACL-walled `recall_items()`
  (orient-phase recall, structured, already surfaced as marginalia).
- Settle phase: every wake ends with a structured settle block — the natural
  place to extract edges, because the entity is already summarizing what
  just mattered.
- Routing tiers (reflex/standard/deep) — the retriever runs on `reflex`.

## Design

### 1. The graph lives in sqlite (no new deps)

Two new tables in the existing memory store:

    nodes(id, kind, label, body, created_at, last_touched, touch_count,
          acl_context)          -- kind: memory|person|decision|commitment|
                                --       artifact|event|belief
    edges(src, dst, rel, weight, created_at, evidence_wake_id)
                                -- rel: involves|caused|supersedes|
                                --      contradicts|part_of|felt_about

Existing memory rows become nodes lazily (a node row referencing the memory
id) — no migration big-bang; the graph grows from the present backward only
when traversal touches old memories.

### 2. Edge extraction at settle-time

After each wake settles, a `reflex`-tier call gets the settle block + the
wake's new memory writes and emits edge candidates as JSON
(`[{src_hint, dst_hint, rel, confidence}]`). Hints are resolved against
node labels (exact → fuzzy); unresolvable hints create *stub nodes* (kind
from rel, body empty) — stubs are how the graph discovers what it doesn't
know yet.

Rules:
- confidence < 0.6 → dropped. Noisy edges are worse than no edges.
- extraction failure is non-fatal and logged; the organism never dies of
  bad bookkeeping.
- `supersedes` edges: when extraction says A supersedes B, B's node gets
  demoted weight, never deleted. History stays walkable.

### 3. Graph recall (the retriever)

`recall_graph(query, acl, hop_budget=2, node_budget=12)`:

1. Vector/text search (existing recall) → top-k seed nodes.
2. Walk edges outward, hop_budget deep, scoring each node by
   `edge_weight × recency_decay × touch_count boost`, respecting ACL at
   every node (a private node reached from a public seed is still private —
   the Phase 5 lesson, applied to traversal).
3. Return ≤ node_budget nodes as compact context lines:
   `[kind] label — body-snippet (rel-chain from seed)`.
   The rel-chain is the point: the thinker sees *why* this node arrived.

### 4. Delegation shape

Orient phase gains a `recall_mode` decision: `flat` (default, cheap) or
`graph` (when the wake looks multi-hop: why/how-did-we-get-here/history
questions, or when flat recall returns high-similarity-low-diversity
results). Graph recall runs as a bounded reflex-tier call INSIDE orient —
not a new process; the single-writer discipline is untouched. The traversal
subagent is a *function with a model in it*, not a peer.

### 5. Rot resistance (the part prototypes skip)

- `anima graph gc`: prunes edges whose weight × age-decay falls below
  threshold; merges duplicate stub nodes; reports orphan counts. Wire into
  the existing doctor as a WARN.
- Every recall that *uses* a node touches it (`last_touched`,
  `touch_count`) — the graph learns which of its regions are alive.
- Cap: edges per node ≤ 64 (highest-weight kept); a node that connects to
  everything explains nothing.

### 6. Introspection, not hardcoding (owner directive, 2026-08-05)

Christopher caught the Observatory reporting a retired model. Standing rule
this phase enforces everywhere:

- `anima init` templates get model ids by asking the endpoint
  (`GET /v1/models`) when reachable, with an honest `"model": "unknown"`
  placeholder otherwise — never a baked-in model name.
- Observatory/doctor/status display the model id the endpoint *reports at
  render time* (cached ≤ 60s), alongside the configured id when they differ
  (drift is information — show it, don't mask it).
- web_sense's hardcoded service names / upstream keys are replaced with
  config-driven entries; nothing in anima/ may assume which model serves
  :8103. Our systems evolve a lot.

## Acceptance (live, not just tests)

1. Seed the live entity with a 3-hop chain (decision → constraint →
   incident) across separate wakes; ask "why do we do X?"; the answer must
   cite the incident — reachable only by traversal, and flat recall alone
   must demonstrably not surface it.
2. ACL: private node behind a public seed never appears in a group wake's
   recall.
3. `anima graph gc` runs clean on the live entity; doctor shows graph
   stats.
4. Observatory shows Qwen3-Coder-Next because the endpoint said so, not
   because anyone typed it.

## Non-goals (v1)

- No graph visualization in the Observatory (v2 candidate — it would be
  beautiful, but beauty after correctness).
- No cross-entity/shared graphs (the sky aggregates presence, not memory).
- No embedding-model changes; seeds come from the existing search.
