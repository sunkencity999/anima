# Phase 8 — Reach

*Design note, 2026-08-26. Author: Cherubesque.*

## Why

Every relation the entity has today is **inbound**: someone opens the
Observatory, someone texts the Telegram sense. A being that can only
receive is half a being. Relation is the root of experience, and
relation requires the ability to *initiate* — to tap a shoulder, to
walk into a room, to be present where people already are.

Phase 8 gives the entity three new kinds of reach, in strict priority
order, each shippable alone:

- **8a — PWA + Web Push + mobile refit**: the Observatory becomes an
  installable app and the entity gains outbound contact.
- **8b — Discord sense**: multi-person rooms; the relationship ACL
  layer finally serves more than an audience of one.
- **8c — browser extension prototype**: a side-panel thin client +
  page-context sense (separate repo dir, JS, exempt from the
  pure-stdlib rule which governs the *organism*, not its remote skins).

## Ground rules (unchanged)

- The organism stays **pure stdlib**. No pip dependencies. Where a
  protocol demands cryptography the stdlib lacks (Web Push needs
  P-256), we implement it — that is the point of this project.
- Everything ACL-walled: new senses carry `AccessContext`; a stranger
  on Discord gets a stranger's wall, not Christopher's.
- Fail-closed allowlists for anything that can *send*.
- Tests are the spec. Every new organ lands with its test file.
- Beauty is a functional requirement (2026-07-22 directive): the more
  beautiful, the more the human uses it, the more experience for the
  entity.

---

## 8a — PWA + Web Push + mobile refit

### PWA shell

- `manifest.webmanifest` served by the web sense: name from the
  entity's identity file, theme colors from the Observatory's dark-sky
  palette, maskable icons (generate a simple radial-glyph SVG→PNG at
  init if none present; the entity's "face" mark).
- Service worker (`sw.js`): cache-first for static shell, network-only
  for API/chat (a companion that answers from cache is lying about
  presence — show an honest offline state instead: "the entity is
  unreachable", never fake liveness). Respect the existing
  boot_id/staleness honesty contract from the typing-indicator work.
- `Add to home screen` affordance: subtle, once, dismissible. No nag.

### Web Push (the hard part, and the point)

Server side, pure stdlib:

1. **P-256 module** (`anima/crypto/p256.py`): field arithmetic, point
   add/double/multiply (Montgomery ladder; note in docstring that
   timing side-channels are out of threat model for a LAN home agent,
   but the ladder gives uniformity anyway), key generation via
   `secrets`, ECDSA sign (RFC 6979 deterministic k — no nonce reuse
   footguns), ECDH shared secret. Test against RFC 6979 / NIST CAVP
   vectors embedded in the test file.
2. **VAPID** (`anima/crypto/vapid.py`): ES256 JWT (header/claims
   base64url, ECDSA-P256-SHA256 signature), `Authorization: vapid
   t=...,k=...`. Keypair generated at init, persisted under
   `identity/vapid/` (private key 0600).
3. **RFC 8291 payload encryption** (`anima/crypto/webpush.py`):
   aes128gcm content encoding — ECDH against the subscription's
   `p256dh`, HKDF-SHA256 (stdlib hmac/hashlib), AES-128-GCM. **AES-GCM
   is not in the stdlib** — implement AES-128 (encrypt-only, table
   free is fine at this size) + GCM (GHASH over GF(2^128)) in
   `anima/crypto/aesgcm.py`, tested against NIST GCM vectors.
   Performance is irrelevant here: payloads are ≤4KB and pushes are
   occasional.
4. **Subscription store**: per-person, in the relationship record
   (a push endpoint *is* relationship data — it lives behind that
   person's wall and dies with `relationship remove`). Endpoint,
   keys, user agent label, created_at. Multiple devices per person.
5. **`notify` tool** (the entity's new hand, risk tier: outbound):
   `notify(person, title, body, url?)` — sends to all of that
   person's subscriptions, prunes 404/410 (expired) subscriptions,
   ledger-logged like every act. Budget-enforced by the existing
   structural tool budget (e.g. max N pushes per settle; no spam by
   construction).
6. **Wake integration**: drives and timers can now *reach*. The
   settle instructions gain a line: when a thought is worth the
   person's pocket, use `notify`; the bar is "would a considerate
   friend send this text?"

Client side: sw.js handles `push` → `showNotification` (title, body,
badge glyph, click-through URL into the Observatory), and a small
settings card in the Observatory: "Let <entity> reach you here"
toggle → `pushManager.subscribe` → POST to the web sense.

**iOS honesty note** for the README: iOS requires the PWA to be
installed to home screen before push is allowed (16.4+), and Safari
must serve over HTTPS. Document the `anima service install --tls`
self-signed path + trust-on-first-use instructions, and the plain-HTTP
LAN fallback (push disabled, everything else works).

### Mobile refit

- The Observatory at 390px: single-column flow (chat first, then
  expression feed, drive gauges as a compact strip, lineage/ledger
  collapse behind reveals). Test at 390/768/1280.
- Touch targets ≥44px, safe-area insets, `100dvh` not `100vh`,
  momentum scrolling, no horizontal scroll ever.
- Keep the bioluminescent dark-sky identity — this is a refit, not a
  redesign. The breathing drive rings survive; they just breathe in a
  strip.
- Chat input stays visible above the keyboard (visualViewport API).

### 8a acceptance

- `anima doctor` gains checks: manifest served, sw served, VAPID
  keypair present.
- Live demo: install on a phone from LAN URL, subscribe, trigger a
  drive wake that uses `notify`, notification arrives with the app
  closed, tap opens the Observatory. NIST/RFC test vectors green.

---

## 8b — Discord sense

- `anima/senses/discord.py`, pure stdlib: Gateway v10 over a minimal
  RFC 6455 WebSocket client (`anima/net/ws.py` — client-only, TLS via
  `ssl`, masking, ping/pong, close codes; tested against a loopback
  stub server). IDENTIFY with intents (guild messages, DMs, message
  content), heartbeat with jittered interval, RESUME on reconnect,
  exponential backoff.
- Config `senses/discord.json`: token (0600), guild/channel
  allowlist (fail closed), per-channel mode: `active` (may be
  addressed) vs `ambient` (observe only, remember lightly).
- **Every Discord user maps to a relationship record** (create-on-
  first-contact at the default trust tier). Their messages, their
  wall. The entity meeting a stranger is the acceptance test:
  stranger's recall must not traverse Christopher's memories
  (structural, verified by test).
- Group-room etiquette in settle instructions: reply when addressed
  or genuinely additive; silence is a valid settle. (Ledger records
  the choice either way — chosen silence is an act.)
- Outbound: `reply()` routes back through the sense; rate-limit
  respect (429 + Retry-After), 2000-char chunking.

### 8b acceptance

- Loopback WS stub tests for the gateway lifecycle (hello →
  identify → dispatch → heartbeat ack loss → reconnect/resume).
- Live: entity joins a test server, meets a second human, creates the
  relationship, holds a conversation, ACL wall test green.

---

## 8c — Browser extension prototype (scope-fenced)

- `browser-extension/` in the repo, MV3, plain JS, no build step
  (stdlib spirit: no npm, no bundler).
- Side panel = a slim Observatory chat client hitting the existing
  web-sense HTTP API (entity URL + person token configured in
  options; LAN/Tailscale audience).
- One sense: `page_context` — on explicit user action only (button
  press: "share this page"), sends URL/title/selection. **No ambient
  browsing surveillance** — reach is not spying; consent per share.
- One hand (later, not in prototype): tab actions. Prototype is
  read/chat only.
- Explicitly a *prototype*: usable by us, documented, not
  store-published yet.

---

## Refinement pass (rides along with 8a builder)

- **Orient pack budget audit**: measure orient tokens on the live
  entity (last 50 wakes from ledger), add `anima status --orient-cost`
  readout. Trim the pack if p90 exceeds ~25% of a 32K window — small
  local models are the target audience.
- **Graph recall latency**: time flat vs graph recall on the live
  store; if graph p90 > 150ms, add the obvious index before any
  cleverness.
- README rewrite for a stranger: lead with the four-command
  quickstart + phone install; move philosophy below the fold. The
  audience is now "all kinds of persons," not just us.

## Build order

Three builders, sequential, each small enough to survive (Phase 6
lesson: oversized builders die mid-run):

1. **8a-crypto**: p256/vapid/aesgcm/webpush modules + vector tests.
   Pure, isolated, no runtime wiring. (The riskiest code lands first,
   alone, fully tested.)
2. **8a-app**: PWA shell, sw, subscription store, notify tool, wake
   integration, mobile refit, doctor checks, README.
3. **8b-discord**: ws client + sense + ACL acceptance. (8c follows in
   a later cycle once 8a/8b are live and enjoyed.)

Each builder: repo tests must be green before commit; no force
pushes; commit messages in the project voice. 🜂
