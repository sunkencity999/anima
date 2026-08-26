"""Web Push message encryption and delivery (RFC 8291 / RFC 8188).

The whole pipeline from payload bytes to a POST at the push service:

1. Fresh ephemeral P-256 keypair; ECDH against the subscription's
   `p256dh` public key.
2. HKDF-SHA256 (RFC 5869, built here from stdlib hmac — extract then
   expand) twice: first mixing the ECDH secret with the subscription's
   16-byte `auth` secret and the "WebPush: info" context to get the
   input keying material, then deriving the 16-byte content-encryption
   key and 12-byte nonce under the aes128gcm labels.
3. aes128gcm content coding (RFC 8188): an 86-byte header
   (salt ‖ record size ‖ keyid length ‖ ephemeral public key) followed
   by one AES-128-GCM record; the plaintext carries a 0x02 padding
   delimiter (final record) before sealing.

Encryption is a single record — push payloads are capped at 4 KB by
the services anyway, so the multi-record machinery of RFC 8188 is
deliberately not implemented.

`encrypt` accepts an injectable ephemeral key and salt so the test
file can reproduce RFC 8291 Appendix A byte-for-byte; production
callers pass neither and get fresh `secrets` randomness every message.

`send_webpush` does the actual POST with urllib, VAPID-authorized. A
404 or 410 from the service means the subscription is gone — the
caller should prune it; we surface that as the literal body "expired".
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple

from . import aesgcm, p256, vapid

SALT_BYTES = 16
RECORD_SIZE = 4096
AUTH_SECRET_BYTES = 16
_CEK_INFO = b"Content-Encoding: aes128gcm\x00"
_NONCE_INFO = b"Content-Encoding: nonce\x00"
_KEY_INFO_PREFIX = b"WebPush: info\x00"
PADDING_DELIMITER = b"\x02"  # final (only) record, RFC 8188 §2

# Max plaintext for one 4096-byte record: header is separate, but the
# record itself holds ciphertext + tag, and plaintext + delimiter must
# fit. Push services enforce ~4 KB total anyway; this is a sanity rail.
MAX_PLAINTEXT = RECORD_SIZE - aesgcm.TAG_BYTES - 1


# --- HKDF-SHA256 (RFC 5869) --------------------------------------------------

def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """HKDF-Extract: PRK = HMAC-SHA256(salt, IKM)."""
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand: OKM of `length` bytes under `info`."""
    if length > 255 * 32:
        raise ValueError("HKDF-SHA256 cannot expand that far")
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes((counter,)),
                         hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """Extract-then-expand in one call."""
    return hkdf_expand(hkdf_extract(salt, ikm), info, length)


# --- RFC 8291 key derivation --------------------------------------------------

def derive_keys(ecdh_secret: bytes, auth_secret: bytes,
                ua_public: bytes, as_public: bytes,
                salt: bytes) -> Tuple[bytes, bytes]:
    """(CEK, nonce) per RFC 8291 §3.3–3.4.

    ua_public / as_public are the 65-byte uncompressed points of the
    user agent (subscription) and application server (ephemeral) keys.
    """
    if len(auth_secret) != AUTH_SECRET_BYTES:
        raise ValueError("auth secret must be 16 bytes")
    if len(salt) != SALT_BYTES:
        raise ValueError("salt must be 16 bytes")
    key_info = _KEY_INFO_PREFIX + ua_public + as_public
    ikm = hkdf(auth_secret, ecdh_secret, key_info, 32)
    prk = hkdf_extract(salt, ikm)
    cek = hkdf_expand(prk, _CEK_INFO, aesgcm.KEY_BYTES)
    nonce = hkdf_expand(prk, _NONCE_INFO, aesgcm.NONCE_BYTES)
    return cek, nonce


def content_coding_header(salt: bytes, as_public: bytes,
                          record_size: int = RECORD_SIZE) -> bytes:
    """aes128gcm header: salt(16) ‖ rs(4) ‖ idlen(1) ‖ keyid (RFC 8188 §2.1).

    For Web Push the keyid is the application server's 65-byte
    uncompressed ephemeral public key (RFC 8291 §4)."""
    return (salt + record_size.to_bytes(4, "big")
            + bytes((len(as_public),)) + as_public)


def encrypt(payload: bytes, p256dh_b64url: str, auth_b64url: str,
            ephemeral_private: Optional[int] = None,
            salt: Optional[bytes] = None) -> bytes:
    """Payload bytes → full aes128gcm message body (header + sealed record).

    `ephemeral_private` and `salt` exist for the RFC 8291 Appendix A
    vector test; leave them None in real use and fresh randomness is
    drawn per message (an ephemeral key must never be reused).
    """
    if len(payload) > MAX_PLAINTEXT:
        raise ValueError(f"payload exceeds one record ({MAX_PLAINTEXT} bytes)")
    ua_point = p256.decode_public_key(vapid.b64url_decode(p256dh_b64url))
    auth_secret = vapid.b64url_decode(auth_b64url)
    if ephemeral_private is None:
        ephemeral_private = p256.generate_private_key()
    if salt is None:
        salt = secrets.token_bytes(SALT_BYTES)
    as_public = p256.encode_public_key(p256.public_key(ephemeral_private))
    ua_public = p256.encode_public_key(ua_point)
    ecdh_secret = p256.ecdh(ephemeral_private, ua_point)
    cek, nonce = derive_keys(ecdh_secret, auth_secret,
                             ua_public, as_public, salt)
    record = aesgcm.seal(cek, nonce, payload + PADDING_DELIMITER)
    return content_coding_header(salt, as_public) + record


# --- delivery ----------------------------------------------------------------

def build_headers(endpoint: str, vapid_keys: Dict[str, str], subject: str,
                  ttl: int) -> Dict[str, str]:
    """The full header set for a Web Push POST."""
    return {
        "TTL": str(ttl),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": vapid.build_authorization(endpoint, vapid_keys,
                                                   subject),
    }


def send_webpush(subscription: Dict[str, object], payload: bytes,
                 vapid_keys: Dict[str, str], ttl: int = 60,
                 subject: str = "mailto:anima@localhost",
                 timeout: float = 10.0) -> Tuple[int, str]:
    """Encrypt `payload` for `subscription` and POST it.

    `subscription` is the browser's PushSubscription JSON:
    {"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}.

    Returns (status_code, body). A 404/410 status returns the literal
    body "expired" — the caller's cue to prune the subscription. A
    network-level failure (no HTTP status at all) returns (0, reason).
    """
    endpoint = str(subscription["endpoint"])
    keys = subscription["keys"]  # type: ignore[index]
    body = encrypt(payload, str(keys["p256dh"]), str(keys["auth"]))
    headers = build_headers(endpoint, vapid_keys, subject, ttl)
    request = urllib.request.Request(endpoint, data=body, headers=headers,
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        status = err.code
        text = err.read().decode("utf-8", "replace")
    except urllib.error.URLError as err:
        return (0, f"unreachable: {err.reason}")
    if status in (404, 410):
        return (status, "expired")
    return (status, text)
