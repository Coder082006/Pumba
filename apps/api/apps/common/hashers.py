"""Argon2id at the parameters SRS §30.2 specifies.

    "Passwords hashed with Argon2id (memory 64 MiB, time cost 3, parallelism
     4) and never logged."

Django's `Argon2PasswordHasher` defaults to 100 MiB / t=2 / p=8, which is not
what the SRS says. The difference is not cosmetic: the work factor is the
entire defence for a stolen password table, and "we used Argon2" without the
parameters is not a claim anyone can audit.

Changing these later is safe. Django re-hashes on the next successful login
when a stored hash's parameters differ from the configured ones, so raising
the cost migrates the table gradually with no reset and no downtime.
"""

from __future__ import annotations

from django.contrib.auth.hashers import Argon2PasswordHasher

__all__ = ["PlatformArgon2PasswordHasher", "MEMORY_COST_KIB", "TIME_COST", "PARALLELISM"]

#: 64 MiB, expressed in KiB because that is argon2-cffi's unit.
MEMORY_COST_KIB = 64 * 1024
TIME_COST = 3
PARALLELISM = 4


class PlatformArgon2PasswordHasher(Argon2PasswordHasher):
    """Argon2id — the variant, not merely the family.

    `Argon2PasswordHasher` already selects the `id` variant; it is named here
    so that a future Django default change is a visible break rather than a
    silent downgrade to `argon2i` or `argon2d`.
    """

    memory_cost = MEMORY_COST_KIB
    time_cost = TIME_COST
    parallelism = PARALLELISM
