"""VAPID (RFC 8292): ES256 JWTs and the Authorization header.

Signing uses the RFC 6979 A.2.5 fixed key so the JWT is fully
deterministic; verification runs twice — once through vapid's own
verifier and once independently re-derived through p256.verify — so a
bug in one path cannot silently vouch for itself.
"""

import hashlib
import json
import re

import pytest

from anima.crypto import p256, vapid

# RFC 6979 A.2.5 key pair — a known-good scalar/point to sign with.
FIXED_PRIVATE = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
FIXED_PUBLIC = (
    0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6,
    0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299,
)
CLAIMS = {"aud": "https://push.example.net",
          "exp": 1893456000,
          "sub": "mailto:anima@example.com"}


class TestB64Url:
    def test_roundtrip_all_lengths(self):
        for size in range(0, 70):
            data = bytes(range(size))
            encoded = vapid.b64url_encode(data)
            assert "=" not in encoded
            assert vapid.b64url_decode(encoded) == data

    def test_urlsafe_alphabet(self):
        encoded = vapid.b64url_encode(bytes([0xFB, 0xEF, 0xFF]))
        assert "+" not in encoded and "/" not in encoded


class TestKeypairLifecycle:
    def test_generate_serialize_load_roundtrip(self):
        keys = vapid.generate_vapid_keys()
        private = vapid.load_private_key(keys["private_key"])
        public = vapid.load_public_key(keys["public_key"])
        assert p256.public_key(private) == public

    def test_public_key_is_uncompressed_point(self):
        keys = vapid.generate_vapid_keys()
        raw = vapid.b64url_decode(keys["public_key"])
        assert len(raw) == 65 and raw[0] == 0x04

    def test_load_rejects_wrong_length_private(self):
        with pytest.raises(ValueError):
            vapid.load_private_key(vapid.b64url_encode(b"\x01" * 31))

    def test_load_rejects_zero_private(self):
        with pytest.raises(ValueError):
            vapid.load_private_key(vapid.b64url_encode(b"\x00" * 32))

    def test_load_rejects_off_curve_public(self):
        bad = b"\x04" + b"\x01" * 64
        with pytest.raises(ValueError):
            vapid.load_public_key(vapid.b64url_encode(bad))


class TestJWT:
    def test_structure_and_claims(self):
        token = vapid.sign_jwt(FIXED_PRIVATE, CLAIMS)
        header_b64, claims_b64, sig_b64 = token.split(".")
        assert json.loads(vapid.b64url_decode(header_b64)) == {
            "typ": "JWT", "alg": "ES256"}
        assert json.loads(vapid.b64url_decode(claims_b64)) == CLAIMS
        assert len(vapid.b64url_decode(sig_b64)) == 64  # raw r||s, not DER

    def test_deterministic_signature(self):
        # RFC 6979 nonces: same key + claims → byte-identical JWT.
        assert vapid.sign_jwt(FIXED_PRIVATE, CLAIMS) == \
            vapid.sign_jwt(FIXED_PRIVATE, CLAIMS)

    def test_verify_own_path(self):
        token = vapid.sign_jwt(FIXED_PRIVATE, CLAIMS)
        assert vapid.verify_jwt(token, FIXED_PUBLIC) == CLAIMS

    def test_verify_independent_path_through_p256(self):
        """Re-derive verification without vapid.verify_jwt."""
        token = vapid.sign_jwt(FIXED_PRIVATE, CLAIMS)
        header_b64, claims_b64, sig_b64 = token.split(".")
        signature = vapid.b64url_decode(sig_b64)
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        signing_input = (header_b64 + "." + claims_b64).encode("ascii")
        digest = hashlib.sha256(signing_input).digest()
        assert p256.verify_digest(FIXED_PUBLIC, digest, (r, s))

    def test_verify_rejects_tampered_claims(self):
        token = vapid.sign_jwt(FIXED_PRIVATE, CLAIMS)
        header_b64, _, sig_b64 = token.split(".")
        forged = dict(CLAIMS, sub="mailto:mallory@example.com")
        forged_b64 = vapid.b64url_encode(
            json.dumps(forged, separators=(",", ":")).encode())
        assert vapid.verify_jwt(
            f"{header_b64}.{forged_b64}.{sig_b64}", FIXED_PUBLIC) is None

    def test_verify_rejects_tampered_signature(self):
        token = vapid.sign_jwt(FIXED_PRIVATE, CLAIMS)
        head, claims_b64, sig_b64 = token.split(".")
        raw = bytearray(vapid.b64url_decode(sig_b64))
        raw[0] ^= 1
        bad = f"{head}.{claims_b64}.{vapid.b64url_encode(bytes(raw))}"
        assert vapid.verify_jwt(bad, FIXED_PUBLIC) is None

    def test_verify_rejects_wrong_key(self):
        token = vapid.sign_jwt(FIXED_PRIVATE, CLAIMS)
        _, other_public = p256.generate_keypair()
        assert vapid.verify_jwt(token, other_public) is None

    def test_verify_rejects_garbage(self):
        assert vapid.verify_jwt("not.a.jwt", FIXED_PUBLIC) is None
        assert vapid.verify_jwt("nodots", FIXED_PUBLIC) is None


class TestAuthorizationHeader:
    KEYS = {
        "private_key": vapid.b64url_encode(FIXED_PRIVATE.to_bytes(32, "big")),
        "public_key": vapid.b64url_encode(
            p256.encode_public_key(FIXED_PUBLIC)),
    }

    def test_header_shape(self):
        header = vapid.build_authorization(
            "https://push.example.net/send/abc", self.KEYS,
            "mailto:anima@example.com", now=1700000000)
        assert re.fullmatch(
            r"vapid t=[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+,"
            r"k=[A-Za-z0-9_-]+", header)

    def test_claims_content(self):
        header = vapid.build_authorization(
            "https://push.example.net:8443/send/abc", self.KEYS,
            "mailto:anima@example.com", expiry_s=3600, now=1700000000)
        token = header.split("t=")[1].split(",")[0]
        claims = vapid.verify_jwt(token, FIXED_PUBLIC)
        assert claims == {
            "aud": "https://push.example.net:8443",
            "exp": 1700000000 + 3600,
            "sub": "mailto:anima@example.com",
        }

    def test_k_parameter_is_the_public_key(self):
        header = vapid.build_authorization(
            "https://push.example.net/send/abc", self.KEYS,
            "mailto:anima@example.com")
        k = header.split(",k=")[1]
        assert vapid.load_public_key(k) == FIXED_PUBLIC

    def test_origin_extraction(self):
        assert vapid.push_service_origin(
            "https://fcm.googleapis.com/fcm/send/xyz?a=1") == \
            "https://fcm.googleapis.com"

    def test_origin_rejects_junk(self):
        with pytest.raises(ValueError):
            vapid.push_service_origin("ftp://nope.example/x")
        with pytest.raises(ValueError):
            vapid.push_service_origin("not a url")
