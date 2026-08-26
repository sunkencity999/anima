"""AES-128-GCM, pure stdlib.

Python ships SHA and HMAC but no block cipher, and Web Push (RFC 8291)
encrypts payloads with AES-128-GCM. So: AES-128 (encrypt direction
only — GCM is counter mode, decryption never runs the inverse cipher)
plus GCM built on it, with GHASH over GF(2^128).

Deliberately simple and table-light: the S-box is derived at import
from the GF(2^8) inverse + affine transform rather than pasted as a
magic table, and GHASH multiplies bit-by-bit. Performance is
irrelevant at this scale — push payloads are ≤ 4 KB and occasional.
Correctness is everything, so every layer is tested against FIPS-197
and the NIST GCM spec vectors (tests/test_crypto_aesgcm.py).

API: `seal` and `open_` with 12-byte nonces and 16-byte tags, the only
shapes RFC 8291 uses. Tag comparison goes through
`hmac.compare_digest` — constant-time by contract, not by hope.
"""

from __future__ import annotations

import hmac
from typing import List

NONCE_BYTES = 12
TAG_BYTES = 16
KEY_BYTES = 16
BLOCK = 16


# --- AES-128 core -----------------------------------------------------------

def _gf8_mul(a: int, b: int) -> int:
    """Multiplication in GF(2^8) mod x⁸+x⁴+x³+x+1 (0x11B)."""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        b >>= 1
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
    return result & 0xFF


def _build_sbox() -> List[int]:
    """FIPS-197 §5.1.1 S-box: GF(2^8) inverse then affine transform."""
    sbox = [0] * 256
    for value in range(256):
        # inverse via Fermat: a^254 = a^-1 in GF(2^8); 0 maps to 0
        inv = 1
        acc = value
        for bit in bin(254)[2:]:  # square-and-multiply
            inv = _gf8_mul(inv, inv)
            if bit == "1":
                inv = _gf8_mul(inv, acc)
        if value == 0:
            inv = 0
        b = inv
        s = 0x63
        for shift in range(8):
            bit = ((b >> shift) ^ (b >> ((shift + 4) % 8)) ^
                   (b >> ((shift + 5) % 8)) ^ (b >> ((shift + 6) % 8)) ^
                   (b >> ((shift + 7) % 8))) & 1
            s ^= bit << shift
        sbox[value] = s
    return sbox


_SBOX = _build_sbox()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _expand_key(key: bytes) -> List[bytes]:
    """FIPS-197 §5.2 key expansion → 11 round keys of 16 bytes."""
    if len(key) != KEY_BYTES:
        raise ValueError("AES-128 key must be 16 bytes")
    words = [key[i:i + 4] for i in range(0, 16, 4)]
    for i in range(4, 44):
        temp = words[i - 1]
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]  # RotWord
            temp = bytes(_SBOX[b] for b in temp)  # SubWord
            temp = bytes((temp[0] ^ _RCON[i // 4 - 1],)) + temp[1:]
        words.append(bytes(a ^ b for a, b in zip(words[i - 4], temp)))
    return [b"".join(words[4 * r:4 * r + 4]) for r in range(11)]


def _mix_single_column(col: bytes) -> bytes:
    """FIPS-197 §5.1.3 MixColumns on one column."""
    a0, a1, a2, a3 = col
    return bytes((
        _gf8_mul(a0, 2) ^ _gf8_mul(a1, 3) ^ a2 ^ a3,
        a0 ^ _gf8_mul(a1, 2) ^ _gf8_mul(a2, 3) ^ a3,
        a0 ^ a1 ^ _gf8_mul(a2, 2) ^ _gf8_mul(a3, 3),
        _gf8_mul(a0, 3) ^ a1 ^ a2 ^ _gf8_mul(a3, 2),
    ))


def encrypt_block(round_keys: List[bytes], block: bytes) -> bytes:
    """One AES-128 block encryption (state is column-major per FIPS-197)."""
    if len(block) != BLOCK:
        raise ValueError("AES block must be 16 bytes")
    state = bytes(a ^ b for a, b in zip(block, round_keys[0]))
    for rnd in range(1, 11):
        state = bytes(_SBOX[b] for b in state)  # SubBytes
        # ShiftRows: state[r + 4c]; row r rotates left by r
        s = list(state)
        state = bytes(s[(i + 4 * (i % 4)) % 16] for i in range(16))
        if rnd < 10:  # MixColumns (skipped in the final round)
            state = b"".join(_mix_single_column(state[c:c + 4])
                             for c in range(0, 16, 4))
        state = bytes(a ^ b for a, b in zip(state, round_keys[rnd]))
    return state


# --- GHASH over GF(2^128) ---------------------------------------------------

_R = 0xE1000000000000000000000000000000


def _gf128_mul(x: int, y: int) -> int:
    """Multiplication in GF(2^128) per the GCM spec (bit-serial)."""
    z = 0
    v = y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ _R
        else:
            v >>= 1
    return z


def _ghash(h: int, aad: bytes, ciphertext: bytes) -> bytes:
    """GHASH_H(A, C): padded A, padded C, then the 64+64-bit lengths."""
    y = 0
    for chunk in (aad, ciphertext):
        for i in range(0, len(chunk), BLOCK):
            block = chunk[i:i + BLOCK].ljust(BLOCK, b"\x00")
            y = _gf128_mul(y ^ int.from_bytes(block, "big"), h)
    lengths = (len(aad) * 8).to_bytes(8, "big") + \
              (len(ciphertext) * 8).to_bytes(8, "big")
    y = _gf128_mul(y ^ int.from_bytes(lengths, "big"), h)
    return y.to_bytes(BLOCK, "big")


# --- GCM --------------------------------------------------------------------

def _ctr_stream(round_keys: List[bytes], j0: bytes, length: int) -> bytes:
    """Counter-mode keystream starting at inc32(J0)."""
    counter = int.from_bytes(j0[12:], "big")
    prefix = j0[:12]
    out = bytearray()
    while len(out) < length:
        counter = (counter + 1) & 0xFFFFFFFF
        out += encrypt_block(round_keys, prefix + counter.to_bytes(4, "big"))
    return bytes(out[:length])


def seal(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """AES-128-GCM encrypt → ciphertext || 16-byte tag."""
    if len(nonce) != NONCE_BYTES:
        raise ValueError("GCM nonce must be 12 bytes")
    round_keys = _expand_key(key)
    h = int.from_bytes(encrypt_block(round_keys, b"\x00" * BLOCK), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    ciphertext = bytes(a ^ b for a, b in
                       zip(plaintext, _ctr_stream(round_keys, j0, len(plaintext))))
    s = _ghash(h, aad, ciphertext)
    tag = bytes(a ^ b for a, b in zip(encrypt_block(round_keys, j0), s))
    return ciphertext + tag


def open_(key: bytes, nonce: bytes, sealed: bytes, aad: bytes = b"") -> bytes:
    """AES-128-GCM decrypt-and-verify; raises ValueError on a bad tag.

    Tag check happens before any plaintext is returned, via
    hmac.compare_digest (constant-time).
    """
    if len(nonce) != NONCE_BYTES:
        raise ValueError("GCM nonce must be 12 bytes")
    if len(sealed) < TAG_BYTES:
        raise ValueError("sealed message shorter than the tag")
    ciphertext, tag = sealed[:-TAG_BYTES], sealed[-TAG_BYTES:]
    round_keys = _expand_key(key)
    h = int.from_bytes(encrypt_block(round_keys, b"\x00" * BLOCK), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    s = _ghash(h, aad, ciphertext)
    expected = bytes(a ^ b for a, b in zip(encrypt_block(round_keys, j0), s))
    if not hmac.compare_digest(expected, tag):
        raise ValueError("GCM tag verification failed")
    return bytes(a ^ b for a, b in
                 zip(ciphertext, _ctr_stream(round_keys, j0, len(ciphertext))))
