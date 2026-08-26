"""AES-128 + GCM vectors.

- FIPS-197 Appendix C.1 known-answer for the raw block cipher.
- NIST GCM spec (McGrew/Viega) test cases 1-4 for AES-128-GCM,
  copied from the canonical published values.
"""

import pytest

from anima.crypto import aesgcm

# NIST GCM spec test cases 3/4 share this key and nonce.
K96 = bytes.fromhex("feffe9928665731c6d6a8f9467308308")
IV96 = bytes.fromhex("cafebabefacedbaddecaf888")
PT64 = bytes.fromhex(
    "d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
    "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255")
CT64 = bytes.fromhex(
    "42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
    "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091473f5985")
AAD20 = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")


class TestAES128Block:
    def test_fips197_appendix_c1(self):
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plaintext = bytes.fromhex("00112233445566778899aabbccddeeff")
        expected = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
        rk = aesgcm._expand_key(key)
        assert aesgcm.encrypt_block(rk, plaintext) == expected

    def test_key_length_enforced(self):
        with pytest.raises(ValueError):
            aesgcm._expand_key(b"\x00" * 15)

    def test_block_length_enforced(self):
        rk = aesgcm._expand_key(b"\x00" * 16)
        with pytest.raises(ValueError):
            aesgcm.encrypt_block(rk, b"\x00" * 15)


class TestNISTGCMVectors:
    def test_case_1_empty_everything(self):
        sealed = aesgcm.seal(b"\x00" * 16, b"\x00" * 12, b"")
        assert sealed == bytes.fromhex("58e2fccefa7e3061367f1d57a4e7455a")

    def test_case_2_single_zero_block(self):
        sealed = aesgcm.seal(b"\x00" * 16, b"\x00" * 12, b"\x00" * 16)
        assert sealed[:16] == bytes.fromhex("0388dace60b6a392f328c2b971b2fe78")
        assert sealed[16:] == bytes.fromhex("ab6e47d42cec13bdf53a67b21257bddf")

    def test_case_3_four_blocks_no_aad(self):
        sealed = aesgcm.seal(K96, IV96, PT64)
        assert sealed[:-16] == CT64
        assert sealed[-16:] == bytes.fromhex("4d5c2af327cd64a62cf35abd2ba6fab4")

    def test_case_4_truncated_plaintext_with_aad(self):
        sealed = aesgcm.seal(K96, IV96, PT64[:60], AAD20)
        assert sealed[:-16] == CT64[:60]
        assert sealed[-16:] == bytes.fromhex("5bc94fbc3221a5db94fae95ae7121a47")

    def test_case_3_opens(self):
        sealed = CT64 + bytes.fromhex("4d5c2af327cd64a62cf35abd2ba6fab4")
        assert aesgcm.open_(K96, IV96, sealed) == PT64

    def test_case_4_opens_with_aad(self):
        sealed = CT64[:60] + bytes.fromhex("5bc94fbc3221a5db94fae95ae7121a47")
        assert aesgcm.open_(K96, IV96, sealed, AAD20) == PT64[:60]


class TestSealOpen:
    def test_roundtrip(self):
        key, nonce = b"k" * 16, b"n" * 12
        for size in (0, 1, 15, 16, 17, 100, 4096):
            msg = bytes(range(256)) * (size // 256 + 1)
            msg = msg[:size]
            assert aesgcm.open_(key, nonce, aesgcm.seal(key, nonce, msg)) == msg

    def test_roundtrip_with_aad(self):
        key, nonce = b"k" * 16, b"n" * 12
        sealed = aesgcm.seal(key, nonce, b"payload", b"context")
        assert aesgcm.open_(key, nonce, sealed, b"context") == b"payload"

    def test_tampered_ciphertext_rejected(self):
        key, nonce = b"k" * 16, b"n" * 12
        sealed = bytearray(aesgcm.seal(key, nonce, b"payload"))
        sealed[0] ^= 1
        with pytest.raises(ValueError):
            aesgcm.open_(key, nonce, bytes(sealed))

    def test_tampered_tag_rejected(self):
        key, nonce = b"k" * 16, b"n" * 12
        sealed = bytearray(aesgcm.seal(key, nonce, b"payload"))
        sealed[-1] ^= 1
        with pytest.raises(ValueError):
            aesgcm.open_(key, nonce, bytes(sealed))

    def test_wrong_aad_rejected(self):
        key, nonce = b"k" * 16, b"n" * 12
        sealed = aesgcm.seal(key, nonce, b"payload", b"context")
        with pytest.raises(ValueError):
            aesgcm.open_(key, nonce, sealed, b"other")

    def test_wrong_key_rejected(self):
        nonce = b"n" * 12
        sealed = aesgcm.seal(b"k" * 16, nonce, b"payload")
        with pytest.raises(ValueError):
            aesgcm.open_(b"K" * 16, nonce, sealed)

    def test_nonce_length_enforced(self):
        with pytest.raises(ValueError):
            aesgcm.seal(b"k" * 16, b"n" * 11, b"payload")
        with pytest.raises(ValueError):
            aesgcm.open_(b"k" * 16, b"n" * 13, b"x" * 16)

    def test_short_sealed_message_rejected(self):
        with pytest.raises(ValueError):
            aesgcm.open_(b"k" * 16, b"n" * 12, b"x" * 15)
