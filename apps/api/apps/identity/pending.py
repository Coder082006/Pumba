"""Where a registration waits before anyone has proved they own the address.

Data-access layer (SRS §8.2 layer 4), like `repositories.py` — but over Redis
rather than PostgreSQL, and that is the whole point of the module.

**Nothing about an unverified registration reaches the database** — ADR 0021.
§28.2.1's SD-01 has `POST /auth/register` insert a `user` row with status
PENDING and email the verification afterwards, which leaves `user` holding
accounts nobody has proved they own: every abandoned signup, every typo'd
address, every bot that finds the form. The amendment is that the `user` table
contains verified accounts only, so the submitted details wait here for the
minutes it takes to type six digits and are then either promoted or forgotten.

**A cache with a TTL rather than a table, and the expiry is the feature.** A
staging table needs a sweeper, and a sweeper that fails leaves exactly the rows
this change exists to prevent. Redis forgets on its own: an abandoned
registration is gone when the code expires, with nothing scheduled and nothing
to go wrong.

**What is lost if Redis restarts is one in-flight registration.** The person
registers again — the same thing they would do if the email had not arrived.
Weighed against a durable row for an account that may never exist, that is the
better failure.

**The password is stored hashed, exactly as `user` would store it.** Argon2id
is applied before anything is written here, so a disclosure of this cache is no
worse than a disclosure of the password column, and holding the plaintext for
later would be very much worse.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from typing import Any

from django.core.cache import cache

__all__ = [
    "PendingRegistration",
    "hash_code",
    "put",
    "get",
    "get_by_token",
    "count_attempt",
    "drop",
]


@dataclass(frozen=True, slots=True)
class PendingRegistration:
    """Everything needed to create the account, once the code checks out."""

    email: str
    password_hash: str
    first_name: str
    last_name: str
    nationality: str | None
    locale: str
    preferred_currency: str
    marketing_opt_in: bool

    #: SHA-256 of `email:code`, never the digits. Bound to the address for the
    #: reason `repositories.hash_code` binds to the account: a bare hash of six
    #: digits is a table of a million entries anyone can precompute in a second.
    code_hash: str

    #: SHA-256 of the emailed link's token. 256 bits, so it carries no attempt
    #: limit — it is not a value anybody guesses.
    token_hash: str

    attempts: int = 0


def _key(email: str) -> str:
    """The cache key for an address.

    Hashed rather than interpolated: a key is the one part of a cache entry
    that turns up in logs, in `KEYS` output and in a monitoring dashboard, and
    a registration in progress should not put somebody's email address in any
    of them.
    """
    return f"registration:{hashlib.sha256(email.lower().encode()).hexdigest()}"


def _token_key(token_hash: str) -> str:
    return f"registration-token:{token_hash}"


def hash_code(email: str, code: str) -> str:
    return hashlib.sha256(f"{email.lower()}:{code}".encode()).hexdigest()


def put(pending: PendingRegistration, *, ttl: timedelta, token_ttl: timedelta) -> None:
    """Store, replacing whatever was there for the same address.

    **Replacing rather than refusing** is deliberate. Somebody who mistypes
    their address, or whose first email never arrives, registers again — and a
    second attempt that met "this email is already registered" would lock them
    out of their own address for the length of the TTL. A real account is a
    different matter and `services.register_tourist` refuses that before
    reaching here.

    The token index is a second key pointing back at the address, because the
    emailed link carries a token and nothing else. It gets the link's longer
    life; the payload gets the code's.
    """
    cache.set(_key(pending.email), asdict(pending), timeout=int(token_ttl.total_seconds()))
    cache.set(
        _token_key(pending.token_hash),
        pending.email,
        timeout=int(token_ttl.total_seconds()),
    )
    # The attempt counter is its own key so it can be incremented atomically.
    # A count read, added to and written back would let two simultaneous
    # guesses both read four and both write five, which would make the limit a
    # function of how many connections an attacker opens.
    cache.set(_attempts_key(pending.email), 0, timeout=int(ttl.total_seconds()))


def _attempts_key(email: str) -> str:
    return f"{_key(email)}:attempts"


def get(email: str) -> PendingRegistration | None:
    raw: Any = cache.get(_key(email))
    if not isinstance(raw, dict):
        return None
    attempts = cache.get(_attempts_key(email))
    return replace(
        PendingRegistration(**raw),
        attempts=int(attempts) if attempts is not None else 0,
    )


def get_by_token(token_hash: str) -> PendingRegistration | None:
    """Resolve the emailed link back to the registration it belongs to."""
    email = cache.get(_token_key(token_hash))
    return get(email) if isinstance(email, str) else None


def count_attempt(email: str) -> int:
    """Record one wrong guess and return the new total.

    `incr` is a single Redis operation, so parallel guesses cannot both see the
    same count. The key is created by `put`, and a missing one — the code has
    expired out from under the guess — is reported as the limit being reached
    rather than as a fresh budget.
    """
    try:
        return int(cache.incr(_attempts_key(email)))
    except ValueError:
        return 1_000_000


def drop(email: str) -> None:
    """Forget a registration, whether it was promoted or burned."""
    pending = get(email)
    if pending is not None:
        cache.delete(_token_key(pending.token_hash))
    cache.delete(_key(email))
    cache.delete(_attempts_key(email))


def matches(pending: PendingRegistration, code: str) -> bool:
    """Constant-time, because a timing oracle on six digits is practical."""
    return hmac.compare_digest(pending.code_hash, hash_code(pending.email, code))
