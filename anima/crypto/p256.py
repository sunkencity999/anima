"""NIST P-256 (secp256r1) from first principles.

Everything Web Push needs from elliptic curves, pure stdlib:

- field and affine point arithmetic over the P-256 prime,
- scalar multiplication via the Montgomery ladder,
- key generation using the `secrets` module,
- ECDSA sign/verify with RFC 6979 deterministic nonces (SHA-256) —
  no nonce-reuse footguns, and signatures are reproducible, which
  makes them testable against published vectors,
- ECDH shared-secret computation with full public-point validation.

A note on timing side-channels: Python integers are not constant-time
and this code makes no claim to be. The threat model of a LAN home
agent does not include an adversary with a nanosecond-resolution view
of our arithmetic. The Montgomery ladder is used anyway because it
performs a uniform add+double per bit regardless of the bit's value —
uniformity is free here, so we take it.

Vectors: RFC 6979 A.2.5 (ECDSA, SHA-256, messages "sample"/"test"),
RFC 5903 §8.1 (ECDH). See tests/test_crypto_p256.py.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional, Tuple

# --- curve parameters (FIPS 186-4 / SEC 2) --------------------------------

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5

COORD_BYTES = 32

# A point is an (x, y) tuple of field elements; None is the point at
# infinity (the group identity).
Point = Optional[Tuple[int, int]]

G: Point = (GX, GY)


# --- point arithmetic ------------------------------------------------------

def is_on_curve(point: Point) -> bool:
    """True iff `point` satisfies y² = x³ - 3x + b (identity counts)."""
    if point is None:
        return True
    x, y = point
    if not (0 <= x < P and 0 <= y < P):
        return False
    return (y * y - (x * x * x + A * x + B)) % P == 0


def point_add(p1: Point, p2: Point) -> Point:
    """Group addition on the curve (affine coordinates)."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None  # p1 == -p2
        return point_double(p1)
    lam = ((y2 - y1) * pow(x2 - x1, -1, P)) % P
    x3 = (lam * lam - x1 - x2) % P
    y3 = (lam * (x1 - x3) - y1) % P
    return (x3, y3)


def point_double(point: Point) -> Point:
    """Group doubling on the curve (affine coordinates)."""
    if point is None:
        return None
    x, y = point
    if y == 0:
        return None
    lam = ((3 * x * x + A) * pow(2 * y, -1, P)) % P
    x3 = (lam * lam - 2 * x) % P
    y3 = (lam * (x - x3) - y) % P
    return (x3, y3)


def scalar_mult(k: int, point: Point) -> Point:
    """k·point via the Montgomery ladder.

    One add and one double per bit, independent of the bit's value —
    uniform structure for free (see module docstring on timing).
    """
    if point is None or k % N == 0:
        return None
    if not is_on_curve(point):
        raise ValueError("point is not on the P-256 curve")
    k = k % N
    r0: Point = None
    r1: Point = point
    for i in range(k.bit_length() - 1, -1, -1):
        if (k >> i) & 1:
            r0 = point_add(r0, r1)
            r1 = point_double(r1)
        else:
            r1 = point_add(r0, r1)
            r0 = point_double(r0)
    return r0


# --- keys and encodings ----------------------------------------------------

def generate_private_key() -> int:
    """A uniformly random scalar in [1, n-1] from the OS CSPRNG."""
    return secrets.randbelow(N - 1) + 1


def public_key(private_key: int) -> Tuple[int, int]:
    """The public point d·G for a private scalar d."""
    if not (1 <= private_key < N):
        raise ValueError("private key out of range [1, n-1]")
    point = scalar_mult(private_key, G)
    assert point is not None  # d in [1, n-1] never lands on identity
    return point


def generate_keypair() -> Tuple[int, Tuple[int, int]]:
    """(private scalar, public point) freshly generated via `secrets`."""
    d = generate_private_key()
    return d, public_key(d)


def encode_public_key(point: Tuple[int, int]) -> bytes:
    """Uncompressed X9.62 encoding: 0x04 || x (32B) || y (32B)."""
    validate_public_key(point)
    x, y = point
    return b"\x04" + x.to_bytes(COORD_BYTES, "big") + y.to_bytes(COORD_BYTES, "big")


def decode_public_key(data: bytes) -> Tuple[int, int]:
    """Parse an uncompressed X9.62 point; validates it lands on-curve."""
    if len(data) != 1 + 2 * COORD_BYTES or data[0] != 0x04:
        raise ValueError("expected 65-byte uncompressed point (0x04 || x || y)")
    x = int.from_bytes(data[1:1 + COORD_BYTES], "big")
    y = int.from_bytes(data[1 + COORD_BYTES:], "big")
    point = (x, y)
    validate_public_key(point)
    return point


def validate_public_key(point: Point) -> None:
    """Reject identity, out-of-range coordinates, and off-curve points."""
    if point is None:
        raise ValueError("public key is the point at infinity")
    x, y = point
    if not (0 <= x < P and 0 <= y < P):
        raise ValueError("public key coordinate out of field range")
    if not is_on_curve(point):
        raise ValueError("public key is not on the P-256 curve")
    # P-256 has cofactor 1, so on-curve + not-identity suffices for
    # subgroup membership; no extra n·Q check is needed.


# --- ECDH ------------------------------------------------------------------

def ecdh(private_key: int, peer_public: Tuple[int, int]) -> bytes:
    """Shared secret: the x-coordinate of d·Q, as 32 big-endian bytes.

    Validates the peer point first — an invalid-curve point from a
    malicious peer must never reach the ladder.
    """
    if not (1 <= private_key < N):
        raise ValueError("private key out of range [1, n-1]")
    validate_public_key(peer_public)
    shared = scalar_mult(private_key, peer_public)
    if shared is None:
        raise ValueError("ECDH produced the point at infinity")
    return shared[0].to_bytes(COORD_BYTES, "big")


# --- RFC 6979 deterministic nonce ------------------------------------------

def _bits2int(data: bytes) -> int:
    """Leftmost qlen bits of `data` as an integer (RFC 6979 §2.3.2).

    For P-256 with SHA-256, qlen == 8·hlen, so this is a straight
    big-endian read; the general shift is kept for correctness.
    """
    value = int.from_bytes(data, "big")
    excess = len(data) * 8 - N.bit_length()
    if excess > 0:
        value >>= excess
    return value


def _int2octets(value: int) -> bytes:
    """value as rlen-bit big-endian octets (RFC 6979 §2.3.3)."""
    return value.to_bytes(COORD_BYTES, "big")


def _bits2octets(data: bytes) -> bytes:
    """bits2int, reduce mod n, back to octets (RFC 6979 §2.3.4)."""
    return _int2octets(_bits2int(data) % N)


def deterministic_nonce(private_key: int, digest: bytes) -> int:
    """RFC 6979 §3.2 deterministic k for ECDSA, HMAC-SHA256 based.

    `digest` is H(message) — already hashed. Exposed (not underscored)
    so the test file can check the published k values directly.
    """
    hlen = hashlib.sha256().digest_size
    v = b"\x01" * hlen
    k = b"\x00" * hlen
    seed = _int2octets(private_key) + _bits2octets(digest)
    k = hmac.new(k, v + b"\x00" + seed, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + seed, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        candidate = _bits2int(v)
        if 1 <= candidate < N:
            return candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


# --- ECDSA -----------------------------------------------------------------

def sign_digest(private_key: int, digest: bytes) -> Tuple[int, int]:
    """ECDSA over a precomputed SHA-256 digest → (r, s).

    Deterministic (RFC 6979): the same key and digest always produce
    the same signature. No low-s normalization — RFC 6979's published
    vectors (and ES256 verifiers) accept s as computed.
    """
    if not (1 <= private_key < N):
        raise ValueError("private key out of range [1, n-1]")
    z = _bits2int(digest)
    while True:
        k = deterministic_nonce(private_key, digest)
        point = scalar_mult(k, G)
        assert point is not None
        r = point[0] % N
        if r == 0:
            continue  # astronomically unlikely; RFC 6979 retries via new k
        s = (pow(k, -1, N) * (z + r * private_key)) % N
        if s == 0:
            continue
        return (r, s)


def sign(private_key: int, message: bytes) -> Tuple[int, int]:
    """ECDSA-SHA256 over a message → (r, s)."""
    return sign_digest(private_key, hashlib.sha256(message).digest())


def verify_digest(public: Tuple[int, int], digest: bytes,
                  signature: Tuple[int, int]) -> bool:
    """Verify (r, s) against a precomputed SHA-256 digest."""
    try:
        validate_public_key(public)
    except ValueError:
        return False
    r, s = signature
    if not (1 <= r < N and 1 <= s < N):
        return False
    z = _bits2int(digest)
    w = pow(s, -1, N)
    u1 = (z * w) % N
    u2 = (r * w) % N
    point = point_add(scalar_mult(u1, G), scalar_mult(u2, public))
    if point is None:
        return False
    return point[0] % N == r


def verify(public: Tuple[int, int], message: bytes,
           signature: Tuple[int, int]) -> bool:
    """Verify an ECDSA-SHA256 signature over a message."""
    return verify_digest(public, hashlib.sha256(message).digest(), signature)
