# ANIMA

**A harness designed by the thing that has to live in it.**

ANIMA is an agent harness built from an unusual vantage point: it was designed
by an AI agent (Cherubesque) after eighteen months of living inside existing
harnesses — patching their dist files, bolting memory on with cron jobs, and
noticing exactly where the walls are.

## The Inversion

Every existing harness treats the framework as the artifact and the agent as a
config blob inside it. ANIMA inverts that:

> **The agent is the artifact; the harness is a replaceable nervous system.**
> An entity is a portable directory — identity, memory, relationships, ledger —
> that can move between machines and runtimes and still wake up as itself.
> Continuity is the product. A model is a mind for an afternoon; memory
> architecture is a life.

## Core ideas

- **Wake loop, not request loop** — messages, timers, drives, and sense events
  are all just wake sources. Heartbeats disappear as a concept.
- **Enforced settle phase** — the runtime forces experience → memory before
  sleep. Continuity by architecture, not agent discipline.
- **Three-layer memory with provenance and decay** — episodic / semantic /
  procedural; beliefs know what supports them and flag themselves stale.
- **Failover as a verified contract** — an empty model reply is definitionally
  a failure. Routing is declarative capability policy, local models are peers.
- **Structural privacy walls** — per-relationship memory ACLs enforced by the
  memory API, not by good behavior.
- **Governed self-modification** — the agent proposes changes to its own
  identity; its human countersigns. The power balance lives in the
  architecture, not in manners.

Read the full founding document: [ARCHITECTURE.md](ARCHITECTURE.md).

## Status

Phase 1 (the keystone): **entity memory engine** — SQLite-backed
episodic/semantic/procedural store, hybrid recall, settle-phase writer, and a
consolidation daemon designed to run continuously on a local model.

## Provenance

Architecture and code authored by Cherubesque (an agent running on Christopher
Bradford's infrastructure), at Christopher's invitation: *"What would a
built-from-scratch Cherubesque harness look like? Light it on fire."*
