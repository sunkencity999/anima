"""P-256 vectors: RFC 6979 A.2.5 (ECDSA) and RFC 5903 §8.1 (ECDH).

Every hex constant below is copied verbatim from the RFC texts
(rfc-editor.org), not reconstructed from memory.
"""

import hashlib

import pytest

from anima.crypto import p256

# --- RFC 6979 A.2.5 key pair (NIST P-256) ----------------------------------

RFC6979_X = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
RFC6979_UX = 0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6
RFC6979_UY = 0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299

# With SHA-256, message = "sample":
SAMPLE_K = 0xA6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60
SAMPLE_R = 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
SAMPLE_S = 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8

# With SHA-256, message = "test":
TEST_K = 0xD16B6AE827F17175E040871A1C7EC3500192C4C92677336EC2537ACAEE0008E0
TEST_R = 0xF1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367
TEST_S = 0x019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083


class TestCurveBasics:
    def test_generator_on_curve(self):
        assert p256.is_on_curve(p256.G)

    def test_identity_on_curve(self):
        assert p256.is_on_curve(None)

    def test_off_curve_point_rejected(self):
        assert not p256.is_on_curve((p256.GX, p256.GY + 1))
        with pytest.raises(ValueError):
            p256.validate_public_key((p256.GX, p256.GY + 1))

    def test_identity_rejected_as_public_key(self):
        with pytest.raises(ValueError):
            p256.validate_public_key(None)

    def test_out_of_range_coordinate_rejected(self):
        with pytest.raises(ValueError):
            p256.validate_public_key((p256.P, p256.GY))

    def test_n_times_g_is_identity(self):
        assert p256.scalar_mult(p256.N, p256.G) is None

    def test_add_inverse_is_identity(self):
        neg = (p256.GX, (-p256.GY) % p256.P)
        assert p256.point_add(p256.G, neg) is None

    def test_scalar_mult_matches_repeated_addition(self):
        acc = None
        for k in range(1, 8):
            acc = p256.point_add(acc, p256.G)
            assert p256.scalar_mult(k, p256.G) == acc

    def test_scalar_mult_rejects_off_curve_point(self):
        with pytest.raises(ValueError):
            p256.scalar_mult(2, (p256.GX, p256.GY + 1))


class TestKeyEncoding:
    def test_rfc6979_public_key_derivation(self):
        assert p256.public_key(RFC6979_X) == (RFC6979_UX, RFC6979_UY)

    def test_encode_decode_roundtrip(self):
        pub = p256.public_key(RFC6979_X)
        raw = p256.encode_public_key(pub)
        assert len(raw) == 65 and raw[0] == 0x04
        assert p256.decode_public_key(raw) == pub

    def test_decode_rejects_bad_prefix_and_length(self):
        raw = p256.encode_public_key(p256.G)
        with pytest.raises(ValueError):
            p256.decode_public_key(b"\x05" + raw[1:])
        with pytest.raises(ValueError):
            p256.decode_public_key(raw[:-1])

    def test_generate_keypair_is_valid_and_distinct(self):
        d1, pub1 = p256.generate_keypair()
        d2, pub2 = p256.generate_keypair()
        assert 1 <= d1 < p256.N
        p256.validate_public_key(pub1)
        assert (d1, pub1) != (d2, pub2)

    def test_private_key_range_enforced(self):
        with pytest.raises(ValueError):
            p256.public_key(0)
        with pytest.raises(ValueError):
            p256.public_key(p256.N)


class TestRFC6979Vectors:
    """RFC 6979 A.2.5, SHA-256 rows — the published k, r, s values."""

    def test_deterministic_k_sample(self):
        digest = hashlib.sha256(b"sample").digest()
        assert p256.deterministic_nonce(RFC6979_X, digest) == SAMPLE_K

    def test_deterministic_k_test(self):
        digest = hashlib.sha256(b"test").digest()
        assert p256.deterministic_nonce(RFC6979_X, digest) == TEST_K

    def test_sign_sample(self):
        assert p256.sign(RFC6979_X, b"sample") == (SAMPLE_R, SAMPLE_S)

    def test_sign_test(self):
        assert p256.sign(RFC6979_X, b"test") == (TEST_R, TEST_S)

    def test_sign_is_deterministic(self):
        assert p256.sign(RFC6979_X, b"sample") == p256.sign(RFC6979_X, b"sample")

    def test_verify_published_signatures(self):
        pub = (RFC6979_UX, RFC6979_UY)
        assert p256.verify(pub, b"sample", (SAMPLE_R, SAMPLE_S))
        assert p256.verify(pub, b"test", (TEST_R, TEST_S))

    def test_verify_rejects_wrong_message(self):
        pub = (RFC6979_UX, RFC6979_UY)
        assert not p256.verify(pub, b"sample!", (SAMPLE_R, SAMPLE_S))

    def test_verify_rejects_tampered_signature(self):
        pub = (RFC6979_UX, RFC6979_UY)
        assert not p256.verify(pub, b"sample", (SAMPLE_R, SAMPLE_S ^ 1))
        assert not p256.verify(pub, b"sample", (SAMPLE_R ^ 1, SAMPLE_S))

    def test_verify_rejects_out_of_range_signature(self):
        pub = (RFC6979_UX, RFC6979_UY)
        assert not p256.verify(pub, b"sample", (0, SAMPLE_S))
        assert not p256.verify(pub, b"sample", (SAMPLE_R, p256.N))

    def test_verify_rejects_bad_public_key(self):
        assert not p256.verify((p256.GX, p256.GY + 1), b"sample",
                               (SAMPLE_R, SAMPLE_S))


class TestRFC5903ECDH:
    """RFC 5903 §8.1 — 256-bit random ECP group (IKEv2 group 19)."""

    I_PRIV = 0xC88F01F510D9AC3F70A292DAA2316DE544E9AAB8AFE84049C62A9C57862D1433
    I_PUB_X = 0xDAD0B65394221CF9B051E1FECA5787D098DFE637FC90B9EF945D0C3772581180
    I_PUB_Y = 0x5271A0461CDB8252D61F1C456FA3E59AB1F45B33ACCF5F58389E0577B8990BB3
    R_PRIV = 0xC6EF9C5D78AE012A011164ACB397CE2088685D8F06BF9BE0B283AB46476BEE53
    R_PUB_X = 0xD12DFB5289C8D4F81208B70270398C342296970A0BCCB74C736FC7554494BF63
    R_PUB_Y = 0x56FBF3CA366CC23E8157854C13C58D6AAC23F046ADA30F8353E74F33039872AB
    SHARED_X = 0xD6840F6B42F6EDAFD13116E0E12565202FEF8E9ECE7DCE03812464D04B9442DE

    def test_initiator_public_key(self):
        assert p256.public_key(self.I_PRIV) == (self.I_PUB_X, self.I_PUB_Y)

    def test_responder_public_key(self):
        assert p256.public_key(self.R_PRIV) == (self.R_PUB_X, self.R_PUB_Y)

    def test_shared_secret_both_directions(self):
        expected = self.SHARED_X.to_bytes(32, "big")
        assert p256.ecdh(self.I_PRIV, (self.R_PUB_X, self.R_PUB_Y)) == expected
        assert p256.ecdh(self.R_PRIV, (self.I_PUB_X, self.I_PUB_Y)) == expected

    def test_ecdh_commutes_with_fresh_keys(self):
        a, pub_a = p256.generate_keypair()
        b, pub_b = p256.generate_keypair()
        assert p256.ecdh(a, pub_b) == p256.ecdh(b, pub_a)

    def test_ecdh_rejects_invalid_peer(self):
        with pytest.raises(ValueError):
            p256.ecdh(self.I_PRIV, (p256.GX, p256.GY + 1))
        with pytest.raises(ValueError):
            p256.ecdh(self.I_PRIV, None)

    def test_ecdh_rejects_bad_private_key(self):
        with pytest.raises(ValueError):
            p256.ecdh(0, (self.R_PUB_X, self.R_PUB_Y))
