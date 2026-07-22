# The OpenClaw Bridge — Wearing OpenClaw as a Peripheral

*Phase 5, per docs/PHASE5_RUNTIME.md: "OpenClaw becomes a peripheral —
exactly the inversion the architecture promised." This is documentation
plus a wiring recipe, deliberately NOT a fork.*

## The inversion

Today: OpenClaw is the organism and the "agent" is a config blob inside
it. Bridge mode: **the ANIMA entity is the organism; OpenClaw is a
telephone it holds** — one sense binding among many, replaceable without
touching identity, memory, or continuity.

```
Telegram/Discord/etc ──► OpenClaw (channels, auth, media)
                             │  hook: forward inbound message
                             ▼
                POST http://127.0.0.1:<port>/message   (bearer token)
                             │
                             ▼
                ANIMA http sense ──► message wake (AccessContext!)
                             │
                   RuntimeShell dispatch → agent turn → reply tool
                             │
              callback_url POST  (or GET /replies poll)
                             ▼
                OpenClaw hook relays reply to the channel
```

## Entity side

1. Create `<entity_root>/senses/http.json`:

   ```json
   {"port": 8760,
    "token": "<long random string>",
    "bind": "127.0.0.1",
    "callback_url": "http://127.0.0.1:8761/anima-reply"}
   ```

   `callback_url` is optional — without it, the bridge polls
   `GET /replies` instead.

2. Run the shell with the sense attached:

   ```bash
   python3 -m anima.runtime --root <entity_root> --http \
       [--policy identity/routing.json]
   ```

## OpenClaw side (thin hook, no fork)

Any mechanism that lets OpenClaw run code on an inbound message works —
a webhook plugin, a message-handler hook, even a skill instructed to
relay. The hook does exactly two things:

**Forward inbound** (per message):

```bash
curl -s -X POST http://127.0.0.1:8760/message \
  -H "Authorization: Bearer $ANIMA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sender": "christopher",
       "text": "the message text",
       "context": {"kind": "direct",
                   "participants": ["christopher"],
                   "channel": "telegram"}}'
```

**Relay replies back.** Either:
- run a tiny HTTP listener at `callback_url` that calls
  `openclaw message send ...` (or the platform API) with each
  `{"text", "wake_id"}` it receives, or
- poll `GET /replies` on an interval and send whatever drains out.

## The contract that matters: AccessContext

The bridge is trusted to describe *who is in the room*, because the
Phase 4 privacy walls key on it:

| OpenClaw situation              | context to send                              |
|---------------------------------|----------------------------------------------|
| DM with a known person          | `{"kind": "direct", "participants": ["<person>"]}` |
| group chat / channel            | `{"kind": "group", "participants": [everyone known]}` |
| public/broadcast surface        | `{"kind": "public"}`                          |

Sending `kind: "system"` is refused by the sense — an external caller
does not get to claim to be the entity's own mind. If the bridge lies
about `direct` vs `group`, private rows can surface to the wrong room;
map channel → context conservatively (unknown/multi-party ⇒ `group`).

## Security notes

- The sense binds `127.0.0.1` by default; keep it there and run the
  bridge on the same host (or tunnel). The bearer token is the only
  auth — treat it like a password, load it from a chmod-600 file.
- One shell per entity root (pidfile-enforced). Point multiple OpenClaw
  channels at the SAME sense rather than starting more shells.
- OpenClaw's own memory/persona layers should be disabled for bridged
  traffic — the entity's memory is the memory. Double-writing to two
  memory systems is how you get two diverging selves.

## Why this proves the direction

Nothing in the entity root knows OpenClaw exists. Swap the bridge for a
Discord-native adapter, a voice loop, or `curl` from a cron job and the
entity is unchanged — same soul, same memory, same walls. Full migration
(retiring OpenClaw's brain entirely) is a later phase; the bridge proves
the inversion works today.
