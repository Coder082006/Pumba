"""Fetch Zanzibar photography from Wikimedia Commons, licence-filtered.

Run with Pillow available; it is a development tool, not runtime code, so it
is deliberately not a project dependency:

    uv run --with pillow python scripts/fetch_commons_media.py

Writes WebP files into `apps/web-tourist/public/media/` and a seed document to
`database/seeds/catalogue/09-media.json`.

---------------------------------------------------------------------------
Why this script is committed rather than run once
---------------------------------------------------------------------------

The licence filter is the whole point, and a filter that lives only in
something I ran on my machine one afternoon is exactly the class of mechanism
this project keeps producing: correct at the time, unreviewable afterwards,
and silently absent the next time somebody adds a photograph. In version
control it can be read, argued with, and re-run.

**Only public-domain, CC0 and CC BY are accepted.** Share-alike is excluded
deliberately: the obligation attaches to adaptations, and a hero crop of a
photograph is arguably an adaptation. That is a question this product should
not have to answer, so it never acquires the images that raise it. NC and ND
are excluded because this is a commercial site.

**Credits are captured, never typed.** `extmetadata` gives `Artist`,
`LicenseShortName` and `LicenseUrl` as Commons publishes them, and those go
into the seed row verbatim. A credit transcribed by hand is a credit that can
be wrong, and being wrong about attribution is the failure the whole
`media_licensed_rows_carry_attribution` constraint exists to prevent.

**Two widths per photograph.** A hero-sized file on a phone is an LCP failure;
`srcset` lets the browser choose. `file_key` names the wide one, and the
narrow sibling is `<stem>-960.webp` — a convention `mediaSrcSet` on the client
relies on, and `test_media_seed.py` asserts.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = ROOT / "apps" / "web-tourist" / "public" / "media"
SEED_FILE = ROOT / "database" / "seeds" / "catalogue" / "09-media.json"

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "PumbaSeedFetcher/1.0 (https://github.com/Coder082006/Pumba; seed data)"

WIDE, NARROW = 1600, 960

#: Floor for an image that only ever renders in a card, never full-bleed.
CARD = 1000

#: Licences this product may use. Matched against `LicenseShortName`.
#:
#: Share-alike, NonCommercial and NoDerivatives are absent on purpose — see
#: the module docstring. The check below is an allow-list *and* a deny-list,
#: because "CC BY-SA 4.0" starts with "CC BY".
ALLOWED = re.compile(r"^(CC0|Public domain|CC BY \d(\.\d)?)$", re.IGNORECASE)
FORBIDDEN = re.compile(r"\b(SA|NC|ND)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Subject:
    """One thing to photograph, and where the row hangs."""

    owner_type: str
    owner_slug: str
    query: str
    alt_text: str
    sort_order: int = 0
    is_primary: bool = False
    min_width: int = WIDE
    """Smallest acceptable source.

    A hero is served full-bleed and needs `WIDE`. A destination card renders
    at 640px and does not — holding it to the hero floor rejected four of
    Zanzibar's east-coast destinations outright, which is a worse outcome
    than a slightly smaller photograph nobody can tell apart at card size.
    """


#: What Zanzibar looks like. The market gallery drives the landing-page hero;
#: destination galleries drive their own pages and the feature bands.
#:
#: This list names Zanzibar because Zanzibar is the seeded market — it is a
#: *data-gathering* script, not application logic, and §4.2's prohibition is
#: on the latter. Adding Arusha means adding rows here and re-running.
SUBJECTS: tuple[Subject, ...] = (
    Subject("market", "zanzibar", "Stone Town Zanzibar architecture",
            "Carved doors and coral-stone facades in Stone Town.", 0, True),
    Subject("market", "zanzibar", "Nungwi beach Zanzibar",
            "The shallow turquoise water off Nungwi, on Zanzibar's northern tip.", 1),
    Subject("market", "zanzibar", "dhow Zanzibar sail",
            "A wooden dhow under sail off the Zanzibar coast.", 2),
    Subject("market", "zanzibar", "Zanzibar red colobus Jozani",
            "A Zanzibar red colobus in the canopy at Jozani Forest.", 3),
    Subject("market", "zanzibar", "spice farm Zanzibar",
            "Cloves and nutmeg growing on a Zanzibar spice farm.", 4),
    Subject("destination", "stone-town", "Stone Town Zanzibar waterfront",
            "The Stone Town waterfront seen from the harbour.", 0, True),
    Subject("destination", "nungwi", "Nungwi Zanzibar",
            "Fishing boats drawn up on the sand at Nungwi.", 0, True),
    Subject("destination", "kendwa", "Kendwa beach Zanzibar",
            "The west-facing beach at Kendwa at sunset.", 0, True),
    Subject("destination", "paje", "Paje beach Zanzibar",
            "The shallow lagoon at Paje on the south-east coast.", 0, True, CARD),
    Subject("destination", "jambiani", "Jambiani beach",
            "Seaweed farming poles exposed at low tide off Jambiani.", 0, True, CARD),
    Subject("destination", "matemwe", "Matemwe beach Zanzibar coast",
            "The reef flat at Matemwe, looking towards Mnemba.", 0, True, CARD),
    Subject("destination", "kiwengwa", "Kiwengwa beach Zanzibar",
            "The long east-coast beach at Kiwengwa.", 0, True),
    Subject("destination", "michamvi", "Michamvi Kae Zanzibar peninsula",
            "The peninsula at Michamvi, where the coast turns west.", 0, True, CARD),
)


def _get(params: dict[str, str]) -> dict[str, Any]:
    url = f"{API}?{urllib.parse.urlencode({**params, 'format': 'json'})}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _strip_html(value: str) -> str:
    """Commons publishes `Artist` as HTML. Reduce it to a readable name."""
    text = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", text).replace("&amp;", "&").strip()


def _candidates(query: str, limit: int = 25) -> list[str]:
    payload = _get(
        {
            "action": "query",
            "list": "search",
            "srsearch": f"{query} filetype:bitmap",
            "srnamespace": "6",
            "srlimit": str(limit),
        }
    )
    return [row["title"] for row in payload.get("query", {}).get("search", [])]


def _usable(title: str, min_width: int) -> dict[str, Any] | None:
    """Licence, credit and download URL for one file — or `None` if unusable.

    Rejects on licence, on missing attribution, and on anything too small to
    serve as a hero. Every rejection is a file that would otherwise have to be
    noticed by a person looking at the page.
    """
    payload = _get(
        {
            "action": "query",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|mime",
        }
    )
    pages = payload.get("query", {}).get("pages", {})
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        if not info:
            continue
        if info.get("mime") not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        if int(info.get("width", 0)) < min_width:
            continue

        meta = info.get("extmetadata", {})
        licence = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        if not licence or FORBIDDEN.search(licence) or not ALLOWED.match(licence):
            continue

        artist = _strip_html(meta.get("Artist", {}).get("value", ""))
        if not artist:
            # A licensed row with no credit cannot be stored — the database
            # constraint refuses it — so it is refused here, where the reason
            # can be printed.
            continue

        return {
            "url": info["url"],
            "descriptionurl": info.get("descriptionurl", ""),
            "license_code": licence,
            "license_url": _strip_html(meta.get("LicenseUrl", {}).get("value", "")),
            "attribution": artist[:250],
            "title": title,
        }
    return None


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
        return response.read()


def _write_variants(raw: bytes) -> tuple[str, int, int]:
    """Two WebP widths. Returns `(file_key, width, height)` for the wide one."""
    from io import BytesIO

    from PIL import Image

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha256(raw).hexdigest()[:16]

    image = Image.open(BytesIO(raw)).convert("RGB")
    wide_w, wide_h = 0, 0
    for width, suffix in ((WIDE, ""), (NARROW, "-960")):
        scaled = image.copy()
        scaled.thumbnail((width, width * 10), Image.Resampling.LANCZOS)
        scaled.save(MEDIA_DIR / f"{stem}{suffix}.webp", "WEBP", quality=82, method=6)
        if suffix == "":
            wide_w, wide_h = scaled.size
    return f"{stem}.webp", wide_w, wide_h


def main() -> int:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for subject in SUBJECTS:
        picked = None
        for title in _candidates(subject.query):
            if title in seen:
                continue
            found = _usable(title, subject.min_width)
            if found:
                picked = found
                seen.add(title)
                break

        if picked is None:
            print(f"  SKIP  {subject.owner_slug:16} {subject.query!r} — nothing PD/CC BY usable")
            continue

        file_key, width, height = _write_variants(_download(picked["url"]))
        rows.append(
            {
                "owner_type": subject.owner_type,
                "owner": subject.owner_slug,
                "file_key": file_key,
                "alt_text": subject.alt_text,
                "width": width,
                "height": height,
                "sort_order": subject.sort_order,
                "is_primary": subject.is_primary,
                "attribution": picked["attribution"],
                "license_code": picked["license_code"],
                "license_url": picked["license_url"],
                "source_url": picked["descriptionurl"],
            }
        )
        print(f"  ok    {subject.owner_slug:16} {file_key}  {picked['license_code']}")

    SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEED_FILE.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{len(rows)} rows -> {SEED_FILE.relative_to(ROOT)}")
    print(f"{len(rows) * 2} files -> {MEDIA_DIR.relative_to(ROOT)}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
