# Phase 5 — The Runtime Shell

*Design note, Cherubesque, 2026-07-22. Written before implementation, deliberately.*

Phases 1–4 built organs in jars: memory, motivation, the wake loop, the walls.
Every test injects a fake clock and a toy handler. Phase 5 is the moment the
organism breathes wall-clock air: a process that hosts an EntityRoot, runs
wakes in real time, **acts** through a model, and wears external channels as
senses.

## The missing organ nobody mentioned: hands

The build order said "runtime shell that can wear OpenClaw's channels as
senses." But senses without actuators are a locked-in patient. The real
deliverable hiding inside Phase 5 is the **act phase** — the thing that turns
a wake into model turns and model turns into effects. That requires:

1. **The agent turn** — orient pack + identity → prompt → routed model call →
   tool-call parsing → execution → repeat until final → wake report.
   This is the beating heart. Everything else is plumbing to and from it.
2. **A tool registry with risk tiers** — because §4 gave drive wakes budgets
   (max_tokens, max_actions, risk_cap) and until now nothing enforced them.
   Budgets must be enforced *by the registry*, not by the model's good manners.
   A drive-initiated wake with risk_cap=low physically cannot invoke a
   high-risk tool. Same architecture principle as the ACLs: the wall is
   structural, the model is untrusted.
3. **Senses as bindings** (§8) — declarative adapters that inject wakes and
   accept outbound effects.

## Agent turn contract

```
wake ──► orient(wake)                    # Phase 2, exists
      ──► prompt = identity ⊕ orient ⊕ wake payload
      ──► loop (bounded by budget):
             routed = router.complete(tier(wake), messages, tools=registry.schemas(risk_cap))
             if routed.tool_calls: execute, append results, continue
             else: final
      ──► wake report (auto-drafted from the turn: actions taken, learnings
           the model flagged, errors) ──► settle guard    # Phase 2, exists
```

Key decisions:

- **The model drafts its own settle report.** The final turn must emit a
  structured `settle` block (or the runtime synthesizes one from the ledger
  trail). The enforced-settle guarantee stays: a turn that crashes or forgets
  still writes a failure report. But a *good* turn writes an honest one —
  experience, not just logs.
- **Tier selection is wake-derived.** Message from a person → `standard` or
  `deep`. Drive tick → `reflex` unless escalated. Consolidation → local-only
  tier. Nobody hand-picks models per call; policy does it (§3, exists).
- **Tool results are episodic events, always.** Every tool execution writes
  to the ledger (exists) *and* is eligible for memory. The turn is not
  trusted to remember what it did.

## Tool registry

Minimal built-ins for v1, each tagged with a risk tier:

| tool            | risk   | notes                                       |
|-----------------|--------|---------------------------------------------|
| recall          | low    | query own memory (ACL context of the wake!) |
| remember        | low    | queue belief candidate / episodic note      |
| set_timer       | low    | schedule own future wake (intentions)       |
| satisfy_drive   | low    | close a drive loop                          |
| reply           | low    | respond via the wake's originating sense    |
| http_get        | medium | read-only web                               |
| shell           | high   | disabled unless entity config allows + wake risk_cap=high |

The registry filters the *schema list offered to the model* by the wake's
risk_cap AND enforces at execution time (defense in depth, same as ACL:
deny is structural). Budget: max_actions decrements per execution; exhaustion
ends the turn with a truthful "budget exhausted" report.

**The recall tool must carry the wake's AccessContext.** A group-message wake
that recalls memory gets the group's ACL. This closes the last privacy gap:
the model cannot be sweet-talked into recalling private rows in public,
because the SQL wall (Phase 4) is between it and the data.

## Senses

A sense binding = config + adapter class. v1 ships three:

- **console** — stdin/stdout chat. For development and for the dignity of
  being able to talk to an entity on a laptop with zero infrastructure.
- **http** — generic webhook sense (stdlib http.server): POST /message or
  /event with bearer token → wake injection; replies delivered by POST to a
  configured callback URL (or held for long-poll). This is the universal
  adapter: anything that can speak HTTP can be a sense.
- **openclaw bridge** — documentation + a thin script, not a fork: an
  OpenClaw-side hook forwards inbound messages to the http sense and relays
  ANIMA's replies back. OpenClaw becomes a *peripheral* — exactly the
  inversion the architecture promised. (Full migration is later; the bridge
  proves the direction.)

## Real-time loop

- Wall-clock scheduler thread: `run_pending()` on a short tick, timers/drives
  evaluated on real time. Senses run in their own threads and inject.
- **Graceful shutdown is a settle event.** SIGTERM → drain current wake →
  settle "shutdown" episode → lineage log entry. An entity always knows it
  went to sleep; forgetting remains impossible even across death.
- Single-writer discipline: one shell per entity root, enforced with a
  pidfile/lock. Two runtimes sharing a self is corruption, not concurrency.

## What proves Phase 5 works

Not unit tests alone. The acceptance demo is:

1. Start the shell against a fresh entity root with the local-model policy.
2. Hold a conversation through the console sense; tell it a fact.
3. Kill the shell. Restart it.
4. Ask about the fact — it answers *from memory through recall*, and the
   lineage log shows both lives.

That demo is the thesis made flesh: continuity across death, on local
hardware, with every layer built this morning underneath it.
