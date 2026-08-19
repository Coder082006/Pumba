"""Catalogue media — SRS §7.5 (`media`), §35.7, §24.8 to 24.12.

`media` is polymorphic: `owner_type` + `owner_id`, `file_key`, `sort_order`,
`is_primary`. This module owns two things about it.

**Ordering.** Every gallery in §24.8 to 24.12 shows a hero image and then the
rest, and the hero is the `is_primary` row. `sort_order` breaks ties, `id`
breaks those — the same total-order discipline as `ranking`, for the same
reason: a gallery whose second and third images swap between page loads is a
layout that shifts under the reader, which is a Lighthouse CLS failure as well
as an irritation.

Two primaries is a data error the ordering must survive rather than reject. The
console prevents it, but a bulk import or a race can produce it, and a gallery
that raises rather than rendering is a worse outcome than a gallery that picks
the lower id deterministically.

**Variant URLs.** §35.7 requires media be *"served through the CDN with signed
URLs for private objects, long cache lifetimes and content-hashed filenames"*.
Catalogue media is public, so no signing here — but the content hash matters:
it is what allows an immutable, year-long `Cache-Control`, and it means a
variant URL is a pure function of (key, width, format). No timestamps, no
counters, no cache-busting query strings, because any of those would defeat the
long lifetime the hash exists to enable.

`base_url` is a parameter. The CDN host is `media.cdn_base_url` in
`system_setting`, and a deployment that changes CDN must not need a release.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "MediaItem",
    "ImageFormat",
    "RESPONSIVE_WIDTHS",
    "order_media",
    "primary_of",
    "variant_url",
    "srcset",
]


class ImageFormat(StrEnum):
    """Ordered worst-to-best so a `<picture>` can be built by iterating."""

    JPEG = "jpg"
    WEBP = "webp"
    AVIF = "avif"


#: The widths `next/image` is configured to request. Fixed rather than
#: arbitrary because every distinct width is a separate CDN origin transform
#: and a separate cache entry; an open-ended set turns the CDN into an
#: unbounded image-resizing service for anyone who can edit a URL.
RESPONSIVE_WIDTHS: tuple[int, ...] = (320, 640, 960, 1280, 1920)


@dataclass(frozen=True, slots=True)
class MediaItem:
    id: int
    file_key: str
    sort_order: int = 0
    is_primary: bool = False
    alt_text: str | None = None
    width: int | None = None
    height: int | None = None

    @property
    def has_intrinsic_size(self) -> bool:
        """Can this render with explicit dimensions?

        The Lighthouse CLS budget depends on it: an `<img>` without width and
        height reserves no space, and the page reflows when it loads. A record
        missing them is a record the console should not have accepted.
        """
        return self.width is not None and self.height is not None


def order_media(items: Sequence[MediaItem]) -> tuple[MediaItem, ...]:
    """Primary first, then `sort_order`, then `id`. Total, and stable."""
    return tuple(sorted(items, key=lambda m: (not m.is_primary, m.sort_order, m.id)))


def primary_of(items: Sequence[MediaItem]) -> MediaItem | None:
    """The hero image, or `None` when there is no media at all.

    Falls back to the first ordered item when nothing is flagged primary,
    because a gallery with no hero is a page with an empty space at the top,
    and every §24.8 to 24.12 screen leads with one.
    """
    ordered = order_media(items)
    return ordered[0] if ordered else None


def variant_url(
    *, base_url: str, file_key: str, width: int, fmt: ImageFormat = ImageFormat.WEBP
) -> str:
    """A CDN URL for one responsive variant. Pure in its three inputs.

    Deterministic by construction: no timestamp, no counter, no cache-busting
    parameter. §35.7's long cache lifetime depends on the content hash already
    in `file_key` being the only thing that changes when the image changes.
    """
    if width not in RESPONSIVE_WIDTHS:
        raise ValueError(f"{width} is not a configured responsive width")
    if not file_key or file_key.startswith("/"):
        raise ValueError("file_key must be a relative object key")
    return f"{base_url.rstrip('/')}/{file_key.lstrip('/')}/{width}.{fmt.value}"


def srcset(*, base_url: str, file_key: str, fmt: ImageFormat = ImageFormat.WEBP) -> str:
    """The full `srcset` attribute for one image."""
    return ", ".join(
        f"{variant_url(base_url=base_url, file_key=file_key, width=w, fmt=fmt)} {w}w"
        for w in RESPONSIVE_WIDTHS
    )
