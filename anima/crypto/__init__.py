"""Pure-stdlib cryptography for Web Push (Phase 8a, PHASE8_REACH.md).

The organism stays pure stdlib. Web Push (RFC 8030/8291/8292) demands
P-256 ECDSA/ECDH, HKDF, and AES-128-GCM — none of which Python ships.
So we implement them, from the primes up, and test every layer against
published RFC/NIST vectors. That is the point of this project: where a
protocol demands cryptography the stdlib lacks, the entity grows the
organ itself.

Modules:
- p256     — NIST P-256 curve: field/point arithmetic, keygen, ECDSA
             (RFC 6979 deterministic nonces), ECDH.
- aesgcm   — AES-128 + GCM (GHASH over GF(2^128)): seal/open.
- vapid    — RFC 8292 voluntary application server identification:
             ES256 JWTs and the `vapid t=...,k=...` header.
- webpush  — RFC 8291 aes128gcm message encryption + the actual POST.

Nothing here is wired into the runtime yet; a later builder does that.
These are organs grown in isolation, fully tested, awaiting a body.
"""
