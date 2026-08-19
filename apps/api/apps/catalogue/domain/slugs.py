"""URL slugs — SRS §7.5.6 (`slug VARCHAR(140) NOT NULL UNIQUE, URL-safe`).

Slugs are the public identity of every SEO page: `/destinations/stone-town`,
`/attractions/jozani-forest`. Two properties matter more than elegance.

**Deterministic.** The same name always produces the same slug, on every
machine and every Python build. So no hashing, no randomness, and no reliance
on locale — `str.lower()` is locale-independent in Python, but a naive
`unicodedata.normalize` round-trip is not obviously so, which is why the fold
is explicit and tested against named characters rather than assumed.

**Market-neutral.** §4.2 prohibits Zanzibar-shaped assumptions, and an ASCII-only
slugifier is one: it happens to work for "Nungwi" and "Stone Town" and silently
produces an empty string for a name in Arabic or Chinese script. Expansion to
East Africa (§4.3) brings Kiswahili and French names with diacritics, and a
later market brings scripts that do not fold to ASCII at all. So the fold
degrades in stages — strip combining marks first, and when nothing survives,
fall back to a stable transliteration of the code points rather than to an
empty slug or an exception at 2 a.m. in an admin form.

Django ships `slugify`. It is not used here because `domain/` may not import
Django (rule 4), and because Django's version returns `""` for a non-Latin name,
which would violate the `NOT NULL UNIQUE` column the moment two such names were
created.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Container

__all__ = ["MAX_SLUG_LENGTH", "slugify_name", "unique_slug", "SlugError"]

#: §7.5.6 gives the column 140 characters. Slugs are truncated, never rejected:
#: an administrator typing a long name should get a working page, not a form
#: error about a field they cannot see.
MAX_SLUG_LENGTH = 140

#: U+2010..U+2015 — hyphen, non-breaking hyphen, figure dash, en dash, em dash,
#: horizontal bar. Built from code points rather than written as literals
#: because several are visually indistinguishable from a plain hyphen in most
#: fonts, and a reviewer cannot check by eye what they cannot see.
_UNICODE_DASHES = {cp: "-" for cp in range(0x2010, 0x2016)}

_SEPARATORS = re.compile(r"[\s_/\\|,:;.-]+")
_DISALLOWED = re.compile(r"[^a-z0-9-]+")
_COLLAPSE = re.compile(r"-{2,}")


class SlugError(ValueError):
    """A name that cannot produce any slug at all."""


def slugify_name(name: str) -> str:
    """A URL-safe, deterministic slug for `name`.

    Raises `SlugError` only for input with no usable characters whatsoever —
    empty, or punctuation alone. Every real name produces something.
    """
    if not name or not name.strip():
        raise SlugError("cannot slugify an empty name")

    folded = _fold(name)
    slug = _SEPARATORS.sub("-", folded.strip().lower())
    slug = _DISALLOWED.sub("", slug)
    slug = _COLLAPSE.sub("-", slug).strip("-")

    if not slug:
        slug = _codepoint_fallback(name)
    if not slug:
        raise SlugError(f"no usable characters in {name!r}")

    return slug[:MAX_SLUG_LENGTH].rstrip("-")


def unique_slug(base: str, *, taken: Container[str], limit: int = 1000) -> str:
    """`base`, or `base-2`, `base-3`, … until one is free.

    The numeric suffix starts at 2, so the first "Paje Beach" is `paje-beach`
    and the second is `paje-beach-2`. Starting at 1 would imply the original
    was also numbered, which reads as a mistake in a URL.

    Truncation keeps the suffix: a 140-character name colliding with another
    must still produce a distinct slug, so the stem is shortened to make room
    rather than the suffix being dropped.
    """
    if base not in taken:
        return base

    for suffix in range(2, limit + 1):
        tail = f"-{suffix}"
        stem = base[: MAX_SLUG_LENGTH - len(tail)].rstrip("-")
        candidate = f"{stem}{tail}"
        if candidate not in taken:
            return candidate

    raise SlugError(f"exhausted {limit} slug candidates for {base!r}")


def _fold(name: str) -> str:
    """Strip combining marks: "Zanzíbar" → "Zanzibar", "Æ" → "AE"."""
    expanded = name.translate(_UNICODE_DASHES)
    expanded = expanded.replace("ß", "ss").replace("æ", "ae").replace("Æ", "AE")
    expanded = expanded.replace("ø", "o").replace("Ø", "O").replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", expanded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _codepoint_fallback(name: str) -> str:
    """A stable slug for a name that folds to nothing in ASCII.

    Deterministic and reversible-looking rather than pretty: a Chinese or
    Arabic destination name yields `u-4e2d-u56fd` rather than an empty string
    or a crash. Ugly URLs are an administrator's problem to fix by supplying a
    slug; a violated NOT NULL constraint is a 500 in production.
    """
    parts = [f"u{ord(ch):x}" for ch in name if not ch.isspace() and ch.isalnum()]
    return "-".join(parts)
