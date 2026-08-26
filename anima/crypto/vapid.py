"""VAPID — Voluntary Application Server Identification (RFC 8292).

A push service will relay a message from anyone holding a valid
subscription URL; VAPID is how the application server signs its work.
We mint an ES256 JWT (RFC 7519 structure, P-256 ECDSA-SHA256 with the
signature as raw r||s — *not* DER) whose claims name the push
service's origin (`aud`), an expiry (`exp`, ≤ 24h out), and a contact
(`sub`). It travels in `Authorization: vapid t=<jwt>,k=<pubkey>`.

Keys are the entity's identity toward the push services: the private
key is a 32-byte P-256 scalar, the public key the 65-byte uncompressed
point, both carried as unpadded base64url. Persistence (under
`identity/vapid/`, mode 0600) is a later builder's job — this module
only generates, serializes, loads, signs, and verifies.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

from . import p256

DEFAULT_EXPIRY_S = 12 * 3600  # RFC 8292 caps exp at 24h; stay well inside


# --- base64url (unpadded, as everything in Web Push uses it) ---------------

def b64url_encode(data: bytes) -> str:
    """Unpadded URL-safe base64."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """URL-safe base64, tolerant of missing padding."""
    padding = -len(text) % 4
    return base64.urlsafe_b64decode(text + "=" * padding)


# --- keypair lifecycle ------------------------------------------------------

def generate_vapid_keys() -> Dict[str, str]:
    """Fresh P-256 keypair as {'private_key', 'public_key'} base64url."""
    private, public = p256.generate_keypair()
    return {
        "private_key": b64url_encode(private.to_bytes(32, "big")),
        "public_key": b64url_encode(p256.encode_public_key(public)),
    }


def load_private_key(private_b64url: str) -> int:
    """32-byte base64url scalar → int, range-checked."""
    raw = b64url_decode(private_b64url)
    if len(raw) != 32:
        raise ValueError("VAPID private key must be 32 bytes")
    value = int.from_bytes(raw, "big")
    if not (1 <= value < p256.N):
        raise ValueError("VAPID private key out of range [1, n-1]")
    return value


def load_public_key(public_b64url: str) -> Tuple[int, int]:
    """65-byte base64url uncompressed point → validated (x, y)."""
    return p256.decode_public_key(b64url_decode(public_b64url))


# --- ES256 JWT --------------------------------------------------------------

def sign_jwt(private_key: int, claims: Dict[str, object]) -> str:
    """Compact-serialization ES256 JWT: b64(header).b64(claims).b64(r||s)."""
    header = {"typ": "JWT", "alg": "ES256"}
    signing_input = (
        b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    )
    r, s = p256.sign(private_key, signing_input.encode("ascii"))
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input + "." + b64url_encode(signature)


def verify_jwt(token: str, public: Tuple[int, int]) -> Optional[Dict[str, object]]:
    """Verify an ES256 JWT; returns the claims dict, or None if invalid."""
    try:
        header_b64, claims_b64, sig_b64 = token.split(".")
        header = json.loads(b64url_decode(header_b64))
        signature = b64url_decode(sig_b64)
        if header.get("alg") != "ES256" or len(signature) != 64:
            return None
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        signing_input = (header_b64 + "." + claims_b64).encode("ascii")
        if not p256.verify(public, signing_input, (r, s)):
            return None
        return json.loads(b64url_decode(claims_b64))
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


# --- the Authorization header -----------------------------------------------

def push_service_origin(endpoint: str) -> str:
    """The `aud` claim: scheme://host[:port] of the push endpoint."""
    parts = urlsplit(endpoint)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError(f"not a usable push endpoint: {endpoint!r}")
    return f"{parts.scheme}://{parts.netloc}"


def build_authorization(endpoint: str, vapid_keys: Dict[str, str],
                        subject: str,
                        expiry_s: int = DEFAULT_EXPIRY_S,
                        now: Optional[int] = None) -> str:
    """`vapid t=<jwt>,k=<public key>` for a POST to `endpoint`.

    `subject` should be a mailto: or https: URI the push service could
    use to reach the operator if the sender misbehaves (RFC 8292 §2.1).
    """
    private = load_private_key(vapid_keys["private_key"])
    claims = {
        "aud": push_service_origin(endpoint),
        "exp": int(now if now is not None else time.time()) + expiry_s,
        "sub": subject,
    }
    token = sign_jwt(private, claims)
    return f"vapid t={token},k={vapid_keys['public_key']}"
