"""CryptoPort — field-level envelope encryption. SRS §30.4.

    "Field-level encryption using envelope encryption for
     tourist_profile.passport_reference, driver.licence_number and
     driver.national_id_ref; keys held in the secrets manager, rotated
     annually, with decryption permitted only in the two service methods that
     legitimately need it, each of which writes an audit entry."

Phase 2 needs it for `user.mfa_secret` (§7.5.1: "Encrypted TOTP seed") and
`tourist_profile.passport_reference`.

**No KMS provider has been selected.** Unlike payment (D1) and routing (D2),
key management has no Appendix D entry at all, so it is proposed as **D8** —
it blocks production launch, not this phase. The port exists now so that the
call sites are written against the final shape and the eventual adapter is a
swap rather than a rewrite.

**The interface is envelope encryption, not "encrypt this string".** Every
ciphertext carries the id of the key that produced it, because annual
rotation means several key versions are live at once and a ciphertext that
cannot name its key is undecryptable after the first rotation. That is the
single most common way field-level encryption is got wrong.

`aad` (additional authenticated data) binds a ciphertext to its row. Passing
the column and the row's public id means a ciphertext lifted from one row and
pasted into another fails to decrypt rather than silently decrypting into the
wrong record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["Ciphertext", "CryptoPort", "DecryptionError"]


class DecryptionError(Exception):
    """Authentication failed, or the key is unavailable.

    Never distinguishes the two to the caller: which one it was is a detail
    an attacker probing a ciphertext would like to know.
    """


@dataclass(frozen=True, slots=True)
class Ciphertext:
    """What gets stored in the BYTEA column."""

    key_id: str
    nonce: bytes
    body: bytes

    def to_bytes(self) -> bytes:
        """A single self-describing blob for one column.

        Length-prefixed rather than delimited, so a key id containing the
        delimiter cannot split the record.
        """
        key = self.key_id.encode("utf-8")
        return (
            len(key).to_bytes(2, "big")
            + key
            + len(self.nonce).to_bytes(1, "big")
            + self.nonce
            + self.body
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> Ciphertext:
        if len(raw) < 3:
            raise DecryptionError("ciphertext is truncated")
        key_len = int.from_bytes(raw[:2], "big")
        offset = 2 + key_len
        if len(raw) < offset + 1:
            raise DecryptionError("ciphertext is truncated")
        nonce_len = raw[offset]
        nonce_start = offset + 1
        body_start = nonce_start + nonce_len
        if len(raw) < body_start:
            raise DecryptionError("ciphertext is truncated")
        return cls(
            key_id=raw[2:offset].decode("utf-8"),
            nonce=raw[nonce_start:body_start],
            body=raw[body_start:],
        )


@runtime_checkable
class CryptoPort(Protocol):
    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> Ciphertext:
        """Encrypt under the current key version."""
        ...

    def decrypt(self, ciphertext: Ciphertext, *, aad: bytes = b"") -> bytes:
        """Decrypt under the key the ciphertext names.

        Raises `DecryptionError` when the key is unknown, the `aad` does not
        match, or the body has been tampered with.
        """
        ...

    @property
    def current_key_id(self) -> str:
        """The key new ciphertexts are written under."""
        ...
