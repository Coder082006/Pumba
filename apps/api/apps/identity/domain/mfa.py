"""TOTP — RFC 6238, for the MFA of SRS §30.2.

    "TOTP MFA is mandatory for PROVIDER_* and all administrative roles and
     optional for tourists and drivers."

Implemented on the standard library rather than on a dependency. It is about
thirty lines of HMAC and truncation, RFC 6238 publishes test vectors for all
three HMAC variants, and those vectors are asserted in
`test_domain_mfa.py` — which is a stronger correctness argument than a
transitive dependency on an unreviewed package for a security primitive.

Three properties this file exists to guarantee:

* **Comparison is constant-time.** `hmac.compare_digest`, never `==`. A
  timing oracle on a six-digit code is a practical attack, not a theoretical
  one.
* **Drift is bounded and symmetric.** One step either side by default, so a
  user whose phone clock is thirty seconds out can still log in and an
  attacker gains no meaningful replay window.
* **The clock is a parameter.** `at` is passed in, so every vector and every
  drift boundary is testable exactly.

Replay prevention — refusing a code already spent in the same step — needs
storage and therefore lives in the service layer, not here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
from datetime import datetime
from urllib.parse import quote, urlencode

__all__ = [
    "DEFAULT_DIGITS",
    "DEFAULT_STEP_SECONDS",
    "totp_code",
    "verify_totp",
    "provisioning_uri",
    "secret_to_base32",
    "base32_to_secret",
]

DEFAULT_STEP_SECONDS = 30
DEFAULT_DIGITS = 6
_ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


def _counter(at: datetime, step_seconds: int) -> int:
    if at.tzinfo is None:
        raise ValueError("`at` must be timezone-aware; SRS §7.2 forbids naive datetimes")
    return int(at.timestamp()) // step_seconds


def _hotp(secret: bytes, counter: int, *, digits: int, algorithm: str) -> str:
    """RFC 4226 §5.3 dynamic truncation."""
    try:
        digestmod = _ALGORITHMS[algorithm]
    except KeyError:
        raise ValueError(f"unsupported algorithm {algorithm!r}") from None

    mac = hmac.new(secret, struct.pack(">Q", counter), digestmod).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def totp_code(
    secret: bytes,
    *,
    at: datetime,
    step_seconds: int = DEFAULT_STEP_SECONDS,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = "SHA1",
) -> str:
    """The code valid for the step containing `at`."""
    if not secret:
        raise ValueError("secret must not be empty")
    if digits < 6 or digits > 10:
        raise ValueError("digits must be between 6 and 10")
    if step_seconds < 1:
        raise ValueError("step_seconds must be positive")
    return _hotp(secret, _counter(at, step_seconds), digits=digits, algorithm=algorithm)


def verify_totp(
    secret: bytes,
    code: str,
    *,
    at: datetime,
    drift_steps: int = 1,
    step_seconds: int = DEFAULT_STEP_SECONDS,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = "SHA1",
) -> bool:
    """Whether `code` is valid at `at`, allowing `drift_steps` either side.

    Every candidate step is evaluated even after a match, so the running time
    does not depend on *which* step matched. Returning early on the first hit
    would leak the direction of the user's clock skew.
    """
    candidate = (code or "").strip()
    if len(candidate) != digits or not candidate.isdigit():
        return False
    if drift_steps < 0:
        raise ValueError("drift_steps must not be negative")

    base = _counter(at, step_seconds)
    matched = False
    for offset in range(-drift_steps, drift_steps + 1):
        expected = _hotp(secret, base + offset, digits=digits, algorithm=algorithm)
        matched |= hmac.compare_digest(expected, candidate)
    return matched


def secret_to_base32(secret: bytes) -> str:
    """Unpadded base32, which is what authenticator apps expect."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def base32_to_secret(encoded: str) -> bytes:
    cleaned = encoded.strip().replace(" ", "").upper()
    padding = "=" * (-len(cleaned) % 8)
    return base64.b32decode(cleaned + padding, casefold=True)


def provisioning_uri(
    *,
    secret: bytes,
    account: str,
    issuer: str,
    digits: int = DEFAULT_DIGITS,
    step_seconds: int = DEFAULT_STEP_SECONDS,
    algorithm: str = "SHA1",
) -> str:
    """The `otpauth://` URI an authenticator app scans.

    The issuer appears twice — in the label and as a parameter — because
    different apps read different ones, and an account shown without an issuer
    is indistinguishable from any other in a user's list.
    """
    label = quote(f"{issuer}:{account}", safe="")
    params = urlencode(
        {
            "secret": secret_to_base32(secret),
            "issuer": issuer,
            "algorithm": algorithm,
            "digits": digits,
            "period": step_seconds,
        }
    )
    return f"otpauth://totp/{label}?{params}"
