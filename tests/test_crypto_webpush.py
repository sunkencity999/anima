"""RFC 8291 Web Push encryption — the Appendix A worked example.

This is the single most important vector in the crypto package: the
RFC publishes every input (subscription keys, auth secret, ephemeral
key, salt) and every intermediate (ECDH secret, IKM, CEK, nonce) plus
the final 144-byte message body. `webpush.encrypt` takes an injectable
ephemeral key + salt precisely so this test can reproduce the RFC
output byte-for-byte. All base64url strings below are copied verbatim
from RFC 8291 §5 and Appendix A (rfc-editor.org).

Also here: RFC 5869 A.1 HKDF-SHA256 vectors and a live-loopback
`send_webpush` test against a stdlib HTTP stub (201 accepted, 410
expired, connection-refused).
"""

import http.server
import threading

import pytest

from anima.crypto import aesgcm, p256, vapid, webpush

# --- RFC 8291 §5 / Appendix A inputs ----------------------------------------

PLAINTEXT_B64 = "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"
AS_PUBLIC_B64 = ("BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg"
                 "Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8")
AS_PRIVATE_B64 = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
UA_PUBLIC_B64 = ("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcx"
                 "aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4")
UA_PRIVATE_B64 = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
SALT_B64 = "DGv6ra1nlYgDCS1FRnbzlw"
AUTH_SECRET_B64 = "BTBZMqHH6r4Tts7J_aSIgg"

# Appendix A intermediates
ECDH_SECRET_B64 = "kyrL1jIIOHEzg3sM2ZWRHDRB62YACZhhSlknJ672kSs"
IKM_B64 = "S4lYMb_L0FxCeq0WhDx813KgSYqU26kOyzWUdsXYyrg"
CEK_B64 = "oIhVW04MRdy2XN9CiKLxTg"
NONCE_B64 = "4h_95klXJ5E_qnoN"
HEADER_B64 = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
              "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8")
CIPHERTEXT_B64 = ("8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHs"
                  "UoEsbI_0LpXMuGvnzQ")
# §5 — the complete message body as carried in the POST
BODY_B64 = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
            "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
            "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def _b64(text: str) -> bytes:
    return vapid.b64url_decode(text)


class TestHKDF:
    """RFC 5869 A.1 — basic HKDF-SHA256 test case."""

    IKM = bytes.fromhex("0b" * 22)
    SALT = bytes.fromhex("000102030405060708090a0b0c")
    INFO = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    PRK = bytes.fromhex("077709362c2e32df0ddc3f0dc47bba63"
                        "90b6c73bb50f9c3122ec844ad7c2b3e5")
    OKM = bytes.fromhex("3cb25f25faacd57a90434f64d0362f2a"
                        "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                        "34007208d5b887185865")

    def test_extract(self):
        assert webpush.hkdf_extract(self.SALT, self.IKM) == self.PRK

    def test_expand(self):
        assert webpush.hkdf_expand(self.PRK, self.INFO, 42) == self.OKM

    def test_combined(self):
        assert webpush.hkdf(self.SALT, self.IKM, self.INFO, 42) == self.OKM

    def test_expand_length_cap(self):
        with pytest.raises(ValueError):
            webpush.hkdf_expand(self.PRK, b"", 255 * 32 + 1)


class TestRFC8291Vector:
    AS_PRIVATE = int.from_bytes(_b64(AS_PRIVATE_B64), "big")
    UA_PRIVATE = int.from_bytes(_b64(UA_PRIVATE_B64), "big")

    def test_key_relationships(self):
        """The published private keys derive the published public points."""
        assert p256.encode_public_key(
            p256.public_key(self.AS_PRIVATE)) == _b64(AS_PUBLIC_B64)
        assert p256.encode_public_key(
            p256.public_key(self.UA_PRIVATE)) == _b64(UA_PUBLIC_B64)

    def test_ecdh_secret(self):
        ua_point = p256.decode_public_key(_b64(UA_PUBLIC_B64))
        assert p256.ecdh(self.AS_PRIVATE, ua_point) == _b64(ECDH_SECRET_B64)
        # and from the receiver's side
        as_point = p256.decode_public_key(_b64(AS_PUBLIC_B64))
        assert p256.ecdh(self.UA_PRIVATE, as_point) == _b64(ECDH_SECRET_B64)

    def test_derived_cek_and_nonce(self):
        cek, nonce = webpush.derive_keys(
            _b64(ECDH_SECRET_B64), _b64(AUTH_SECRET_B64),
            _b64(UA_PUBLIC_B64), _b64(AS_PUBLIC_B64), _b64(SALT_B64))
        assert cek == _b64(CEK_B64)
        assert nonce == _b64(NONCE_B64)

    def test_content_coding_header(self):
        header = webpush.content_coding_header(
            _b64(SALT_B64), _b64(AS_PUBLIC_B64))
        assert len(header) == 86
        assert header == _b64(HEADER_B64)
        # rs = 4096, keyid length = 65
        assert header[16:20] == (4096).to_bytes(4, "big")
        assert header[20] == 65

    def test_full_body_byte_for_byte(self):
        """The one that matters: reproduce RFC 8291 §5 exactly."""
        body = webpush.encrypt(
            _b64(PLAINTEXT_B64), UA_PUBLIC_B64, AUTH_SECRET_B64,
            ephemeral_private=self.AS_PRIVATE, salt=_b64(SALT_B64))
        assert body == _b64(BODY_B64)
        assert body[86:] == _b64(CIPHERTEXT_B64)

    def test_receiver_can_decrypt(self):
        """Close the loop: play the user agent and open the record."""
        body = webpush.encrypt(
            _b64(PLAINTEXT_B64), UA_PUBLIC_B64, AUTH_SECRET_B64,
            ephemeral_private=self.AS_PRIVATE, salt=_b64(SALT_B64))
        salt, keyid = body[:16], body[21:86]
        as_point = p256.decode_public_key(keyid)
        secret = p256.ecdh(self.UA_PRIVATE, as_point)
        cek, nonce = webpush.derive_keys(
            secret, _b64(AUTH_SECRET_B64), _b64(UA_PUBLIC_B64), keyid, salt)
        plaintext = aesgcm.open_(cek, nonce, body[86:])
        assert plaintext.endswith(webpush.PADDING_DELIMITER)
        assert plaintext[:-1] == _b64(PLAINTEXT_B64)
        assert plaintext[:-1] == b"When I grow up, I want to be a watermelon"

    def test_fresh_randomness_differs_but_decrypts(self):
        """Without injection, every message gets a fresh key + salt."""
        body1 = webpush.encrypt(b"hello", UA_PUBLIC_B64, AUTH_SECRET_B64)
        body2 = webpush.encrypt(b"hello", UA_PUBLIC_B64, AUTH_SECRET_B64)
        assert body1 != body2
        for body in (body1, body2):
            salt, keyid = body[:16], body[21:86]
            secret = p256.ecdh(self.UA_PRIVATE,
                               p256.decode_public_key(keyid))
            cek, nonce = webpush.derive_keys(
                secret, _b64(AUTH_SECRET_B64), _b64(UA_PUBLIC_B64),
                keyid, salt)
            assert aesgcm.open_(cek, nonce, body[86:]) == b"hello\x02"

    def test_payload_size_rail(self):
        with pytest.raises(ValueError):
            webpush.encrypt(b"x" * (webpush.MAX_PLAINTEXT + 1),
                            UA_PUBLIC_B64, AUTH_SECRET_B64)

    def test_bad_auth_secret_length_rejected(self):
        with pytest.raises(ValueError):
            webpush.encrypt(b"hi", UA_PUBLIC_B64,
                            vapid.b64url_encode(b"\x00" * 15))


# --- delivery against a loopback stub ----------------------------------------

class _StubPushService(http.server.BaseHTTPRequestHandler):
    status = 201
    seen = None

    def do_POST(self):  # noqa: N802 (stdlib naming)
        length = int(self.headers.get("Content-Length", 0))
        type(self).seen = {
            "path": self.path,
            # urllib title-cases header names (TTL -> Ttl); normalize
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": self.rfile.read(length),
        }
        self.send_response(type(self).status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture()
def stub_service():
    server = http.server.HTTPServer(("127.0.0.1", 0), _StubPushService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)


def _subscription(server) -> dict:
    return {
        "endpoint": f"http://127.0.0.1:{server.server_port}/push/abc123",
        "keys": {"p256dh": UA_PUBLIC_B64, "auth": AUTH_SECRET_B64},
    }


class TestSendWebpush:
    VAPID_KEYS = vapid.generate_vapid_keys()

    def test_delivery_and_headers(self, stub_service):
        _StubPushService.status = 201
        status, body = webpush.send_webpush(
            _subscription(stub_service), b"knock knock",
            self.VAPID_KEYS, ttl=30, subject="mailto:anima@example.com")
        assert status == 201
        seen = _StubPushService.seen
        assert seen["path"] == "/push/abc123"
        assert seen["headers"]["ttl"] == "30"
        assert seen["headers"]["content-encoding"] == "aes128gcm"
        assert seen["headers"]["authorization"].startswith("vapid t=")
        # body is a valid aes128gcm message the subscription can open
        raw = seen["body"]
        salt, keyid = raw[:16], raw[21:86]
        secret = p256.ecdh(
            int.from_bytes(_b64(UA_PRIVATE_B64), "big"),
            p256.decode_public_key(keyid))
        cek, nonce = webpush.derive_keys(
            secret, _b64(AUTH_SECRET_B64), _b64(UA_PUBLIC_B64), keyid, salt)
        assert aesgcm.open_(cek, nonce, raw[86:]) == b"knock knock\x02"

    def test_vapid_jwt_names_the_stub_origin(self, stub_service):
        _StubPushService.status = 201
        webpush.send_webpush(_subscription(stub_service), b"x",
                             self.VAPID_KEYS)
        auth = _StubPushService.seen["headers"]["authorization"]
        token = auth.split("t=")[1].split(",")[0]
        public = vapid.load_public_key(self.VAPID_KEYS["public_key"])
        claims = vapid.verify_jwt(token, public)
        assert claims is not None
        assert claims["aud"] == \
            f"http://127.0.0.1:{stub_service.server_port}"

    def test_410_reports_expired(self, stub_service):
        _StubPushService.status = 410
        status, body = webpush.send_webpush(
            _subscription(stub_service), b"gone?", self.VAPID_KEYS)
        assert (status, body) == (410, "expired")

    def test_404_reports_expired(self, stub_service):
        _StubPushService.status = 404
        status, body = webpush.send_webpush(
            _subscription(stub_service), b"gone?", self.VAPID_KEYS)
        assert (status, body) == (404, "expired")

    def test_unreachable_service(self):
        subscription = {
            "endpoint": "http://127.0.0.1:1/push/nobody-home",
            "keys": {"p256dh": UA_PUBLIC_B64, "auth": AUTH_SECRET_B64},
        }
        status, body = webpush.send_webpush(subscription, b"x",
                                            self.VAPID_KEYS)
        assert status == 0
        assert body.startswith("unreachable:")
