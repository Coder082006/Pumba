"""Tests for the Phase 2 ports and their fakes — SRS §30.2, §30.4."""

from __future__ import annotations

import pytest

from ports.breach import (
    BreachedPasswordPort,
    BreachLookupError,
    password_prefix,
    password_suffix,
)
from ports.crypto import Ciphertext, CryptoPort, DecryptionError
from ports.fakes import FakeBreachedPasswords, FakeCrypto


class TestFakesSatisfyTheirProtocols:
    def test_fake_crypto_is_a_crypto_port(self) -> None:
        assert isinstance(FakeCrypto(), CryptoPort)

    def test_fake_breach_is_a_breach_port(self) -> None:
        assert isinstance(FakeBreachedPasswords(), BreachedPasswordPort)


class TestCiphertextEncoding:
    def test_round_trips_through_a_single_blob(self) -> None:
        original = Ciphertext(key_id="kms-2027-01", nonce=b"123456789012", body=b"opaque")
        assert Ciphertext.from_bytes(original.to_bytes()) == original

    def test_a_key_id_containing_the_delimiter_survives(self) -> None:
        """Length-prefixed rather than delimited, so a ':' or a newline in a
        key id cannot split the record."""
        original = Ciphertext(key_id="a:b\nc|d", nonce=b"n" * 12, body=b"x")
        assert Ciphertext.from_bytes(original.to_bytes()).key_id == "a:b\nc|d"

    @pytest.mark.parametrize("raw", [b"", b"\x00", b"\x00\x05ab"])
    def test_truncated_input_is_refused(self, raw: bytes) -> None:
        with pytest.raises(DecryptionError, match="truncated"):
            Ciphertext.from_bytes(raw)


class TestEnvelopeEncryption:
    def test_round_trips(self) -> None:
        crypto = FakeCrypto()
        assert crypto.decrypt(crypto.encrypt(b"seed-material")) == b"seed-material"

    def test_the_ciphertext_names_its_key(self) -> None:
        """Annual rotation means several versions are live; a ciphertext that
        cannot name its key is undecryptable after the first rotation."""
        crypto = FakeCrypto("kms-2027-01")
        assert crypto.encrypt(b"x").key_id == "kms-2027-01"

    def test_a_retired_key_still_decrypts_after_rotation(self) -> None:
        crypto = FakeCrypto("v1")
        stored = crypto.encrypt(b"passport-ref")
        crypto.rotate("v2")
        assert crypto.decrypt(stored) == b"passport-ref"
        assert crypto.encrypt(b"new").key_id == "v2"

    def test_an_unknown_key_is_refused(self) -> None:
        crypto = FakeCrypto("v1")
        foreign = Ciphertext(key_id="someone-elses-key", nonce=b"n" * 12, body=b"\x00" * 32)
        with pytest.raises(DecryptionError):
            crypto.decrypt(foreign)

    def test_the_plaintext_is_not_stored_verbatim(self) -> None:
        crypto = FakeCrypto()
        assert b"passport" not in crypto.encrypt(b"passport-number-1234").body

    def test_each_call_uses_a_fresh_nonce(self) -> None:
        crypto = FakeCrypto()
        assert crypto.encrypt(b"same").nonce != crypto.encrypt(b"same").nonce


class TestAadBindsCiphertextToItsRow:
    """A ciphertext lifted from one row and pasted into another must fail to
    decrypt rather than silently decrypting into the wrong record."""

    def test_matching_aad_decrypts(self) -> None:
        crypto = FakeCrypto()
        blob = crypto.encrypt(b"secret", aad=b"user:1:mfa_secret")
        assert crypto.decrypt(blob, aad=b"user:1:mfa_secret") == b"secret"

    def test_a_different_row_fails(self) -> None:
        crypto = FakeCrypto()
        blob = crypto.encrypt(b"secret", aad=b"user:1:mfa_secret")
        with pytest.raises(DecryptionError):
            crypto.decrypt(blob, aad=b"user:2:mfa_secret")

    def test_a_different_column_fails(self) -> None:
        crypto = FakeCrypto()
        blob = crypto.encrypt(b"secret", aad=b"user:1:mfa_secret")
        with pytest.raises(DecryptionError):
            crypto.decrypt(blob, aad=b"user:1:passport_reference")

    def test_a_tampered_body_fails(self) -> None:
        crypto = FakeCrypto()
        blob = crypto.encrypt(b"secret")
        tampered = Ciphertext(blob.key_id, blob.nonce, blob.body[:-1] + bytes([blob.body[-1] ^ 1]))
        with pytest.raises(DecryptionError):
            crypto.decrypt(tampered)

    def test_the_error_does_not_say_which_check_failed(self) -> None:
        """Wrong key, wrong aad and tampering are one message: which it was
        is a detail an attacker probing a ciphertext would like to know."""
        crypto = FakeCrypto()
        blob = crypto.encrypt(b"secret", aad=b"a")
        with pytest.raises(DecryptionError) as wrong_aad:
            crypto.decrypt(blob, aad=b"b")
        tampered = Ciphertext(blob.key_id, blob.nonce, blob.body[:-1] + b"\x00")
        with pytest.raises(DecryptionError) as tamper:
            crypto.decrypt(tampered, aad=b"a")
        assert str(wrong_aad.value) == str(tamper.value)


class TestKAnonymityProtocol:
    def test_only_five_characters_leave_the_process(self) -> None:
        """A port taking the whole password — or its whole hash — would be a
        credential-exfiltration channel wearing a security feature's name."""
        assert len(password_prefix("hunter2hunter2")) == 5

    def test_prefix_and_suffix_reassemble_the_digest(self) -> None:
        import hashlib

        expected = hashlib.sha1(b"hunter2hunter2", usedforsecurity=False).hexdigest().upper()
        assert password_prefix("hunter2hunter2") + password_suffix("hunter2hunter2") == expected

    def test_the_suffix_is_never_sent(self) -> None:
        breach = FakeBreachedPasswords()
        breach.suffixes_for_prefix(password_prefix("password1234"))
        assert breach.lookups == [password_prefix("password1234")]
        assert password_suffix("password1234") not in breach.lookups


class TestBreachCorpus:
    def test_tc_003_password_is_in_the_corpus(self) -> None:
        """TC-003 registers with "password1234" and expects a rejection."""
        breach = FakeBreachedPasswords()
        suffixes = breach.suffixes_for_prefix(password_prefix("password1234"))
        assert password_suffix("password1234") in suffixes

    def test_an_unbreached_password_is_absent(self) -> None:
        breach = FakeBreachedPasswords()
        phrase = "correct horse battery staple 4718"
        suffixes = breach.suffixes_for_prefix(password_prefix(phrase))
        assert password_suffix(phrase) not in suffixes

    def test_the_corpus_is_configurable(self) -> None:
        breach = FakeBreachedPasswords(frozenset({"my-own-corpus-entry"}))
        suffixes = breach.suffixes_for_prefix(password_prefix("my-own-corpus-entry"))
        assert password_suffix("my-own-corpus-entry") in suffixes

    def test_lookup_is_case_insensitive_on_the_prefix(self) -> None:
        breach = FakeBreachedPasswords()
        prefix = password_prefix("password1234")
        assert breach.suffixes_for_prefix(prefix.lower()) == breach.suffixes_for_prefix(prefix)


class TestUnavailability:
    def test_an_outage_raises_rather_than_returning_false(self) -> None:
        """A bland False would quietly make every caller fail open, including
        registration, which must fail closed."""
        breach = FakeBreachedPasswords()
        breach.unavailable = True
        with pytest.raises(BreachLookupError):
            breach.suffixes_for_prefix(password_prefix("anything"))
