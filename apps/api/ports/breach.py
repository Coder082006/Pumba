"""BreachedPasswordPort — the credential check of SRS §30.2.

    "Minimum 12 characters, checked against a breached-credential list"

The SRS names no provider and states no failure policy. Both gaps matter, so
both are settled here rather than at each call site.

**The interface is k-anonymity, not "is this password breached".** The port
receives the first five characters of the SHA-1 of the password and gets back
the matching suffixes; the caller does the final comparison locally. That is
the range-query protocol every reputable breach service offers, and it means
the full password hash never leaves the process. A port that took the whole
password — or its whole hash — would be a credential-exfiltration channel
wearing a security feature's name.

SHA-1 is not a security choice here. It is the digest the published corpora
are indexed by, and it is being used as a lookup key against a public list,
not to protect anything.

**The failure policy is the caller's, and it is asymmetric** (Q7):

* Registration and password reset **fail closed** — refuse to set a password
  that could not be checked. The user retries in a minute and loses nothing.
* Login **fails open** — never deny an existing user their own account
  because a third party is down.

That asymmetry is why this port raises rather than returning a bland `False`
on failure: a caller must choose, and a silent `False` would quietly make
every caller fail open.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

__all__ = ["BreachLookupError", "BreachedPasswordPort", "password_prefix", "password_suffix"]

_PREFIX_LENGTH = 5


class BreachLookupError(Exception):
    """The breach corpus could not be consulted.

    Deliberately not a bool: the caller must decide whether this failure is
    fatal, and the answer differs between registration and login.
    """


def _sha1_hex(password: str) -> str:
    return hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()


def password_prefix(password: str) -> str:
    """The five hex characters sent to the provider — 1 in ~1M of the space."""
    return _sha1_hex(password)[:_PREFIX_LENGTH]


def password_suffix(password: str) -> str:
    """The remainder, which never leaves this process."""
    return _sha1_hex(password)[_PREFIX_LENGTH:]


@runtime_checkable
class BreachedPasswordPort(Protocol):
    def suffixes_for_prefix(self, prefix: str) -> frozenset[str]:
        """Every known-breached SHA-1 suffix sharing this prefix.

        Raises `BreachLookupError` when the corpus is unreachable.
        """
        ...
