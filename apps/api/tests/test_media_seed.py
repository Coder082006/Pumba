"""The committed photography, and the licence rule it must obey.

`scripts/fetch_commons_media.py` filters to public-domain, CC0 and CC BY, and
captures each credit from Commons' `extmetadata` rather than from anybody's
typing. That script runs on a developer's machine, occasionally, by hand.

**Which is exactly why the rule is asserted here instead.** A filter that
lives only in the tool that applied it is the shape of mechanism this project
keeps producing: correct when it ran, unreviewable afterwards, and silently
absent the next time a photograph is added by other means. The seed file is
what actually ships; these tests read it.

The licence set is narrower than "whatever Commons allows" on purpose.
Share-alike is excluded because the obligation attaches to adaptations and a
hero crop is arguably an adaptation — a question a commercial tourism site
should not have to answer, so it never acquires the images that raise it.
NonCommercial and NoDerivatives are excluded for the obvious reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from apps.catalogue.models import Media, MediaOwnerType
from tests.test_seed import DEFAULT_ROOT

MEDIA_FILE = DEFAULT_ROOT / "catalogue" / "09-media.json"

#: Public domain, CC0 and CC BY at any version. Nothing else.
PERMITTED = re.compile(r"^(CC0|Public domain|CC BY \d(\.\d)?)$", re.IGNORECASE)

#: Present in the *name* of every licence this product may not use.
FORBIDDEN = re.compile(r"\b(SA|NC|ND)\b", re.IGNORECASE)


def _rows() -> list[dict]:
    if not MEDIA_FILE.is_file():
        return []
    return json.loads(MEDIA_FILE.read_text(encoding="utf-8"))


class TestTheCommittedFile:
    def test_there_is_photography_to_check(self) -> None:
        """Guards every assertion below.

        A file that vanished would make the licence tests pass over an empty
        list, which is the failure mode of every "for row in rows: assert"
        test ever written.
        """
        assert len(_rows()) >= 5, "no media seed — the licence checks below prove nothing"

    @pytest.mark.parametrize("field", ["attribution", "license_code", "license_url", "source_url"])
    def test_every_row_carries_its_provenance(self, field: str) -> None:
        for row in _rows():
            assert row.get(field), f"{row.get('file_key')} has no {field}"

    def test_no_row_carries_a_licence_this_product_may_not_use(self) -> None:
        for row in _rows():
            code = row["license_code"]
            assert not FORBIDDEN.search(code), f"{row['file_key']} is {code} — share-alike/NC/ND"
            assert PERMITTED.match(code), f"{row['file_key']} is {code}, which is not on the list"

    def test_every_row_declares_its_intrinsic_size(self) -> None:
        """`next/image` and the §29 CLS budget both need it, and
        `to_media_dto` drops a row without both — so a seed row missing them
        is a photograph that silently never appears."""
        for row in _rows():
            assert row["width"] > 0 and row["height"] > 0, row["file_key"]

    def test_every_row_has_alt_text(self) -> None:
        """A curated photograph with no description is an accessibility
        defect, and these are curated one at a time by hand."""
        for row in _rows():
            assert row["alt_text"].strip(), row["file_key"]

    def test_owner_types_are_ones_the_model_knows(self) -> None:
        valid = {choice.value for choice in MediaOwnerType}
        for row in _rows():
            assert row["owner_type"] in valid, row["owner_type"]

    def test_at_most_one_primary_per_owner(self) -> None:
        """`media_one_primary_per_owner` refuses a second at the database, so
        a seed file with two would fail the load with a constraint error
        rather than a message about the file. Caught here, where the message
        can name the owner."""
        primaries: dict[tuple[str, str], int] = {}
        for row in _rows():
            if row["is_primary"]:
                key = (row["owner_type"], row["owner"])
                primaries[key] = primaries.get(key, 0) + 1
        duplicated = {k: v for k, v in primaries.items() if v > 1}
        assert not duplicated, f"more than one primary image for {duplicated}"


def find_media_dir(start: Path) -> Path:
    """Where the committed photography lives, in either layout.

    The same two-layout problem `find_seed_root` documents, and solved the
    same way rather than with a fixed `parents[N]`. In a checkout the files
    are `<repo>/apps/web-tourist/public/media`; in the api container the
    repository is not mounted above `/app`, so compose bind-mounts them at
    `/media-files` read-only. A fixed depth satisfies exactly one, and the
    one it fails is silent — the test would skip or error rather than check.
    """
    container = Path("/media-files")
    if container.is_dir():
        return container
    candidates = tuple(
        parent / "apps" / "web-tourist" / "public" / "media" for parent in start.resolve().parents
    )
    return next((c for c in candidates if c.is_dir()), candidates[-1])


class TestTheFilesOnDisk:
    """The seed names files; the files have to be there.

    A row pointing at a missing file renders a broken image — which looks like
    a styling bug and is a content one, and is the sort of thing nobody
    notices until a stranger does.
    """

    MEDIA_DIR = find_media_dir(Path(__file__))

    def test_the_wide_file_exists_for_every_row(self) -> None:
        for row in _rows():
            assert (self.MEDIA_DIR / row["file_key"]).is_file(), row["file_key"]

    def test_the_narrow_variant_exists_for_every_row(self) -> None:
        """`mediaSrcSet` on the client names `<stem>-960.webp` by convention.
        The convention lives in the fetch script and in that function, so this
        is what stops the two drifting apart — a missing variant is a 404 the
        browser swallows, falling back to the hero on a phone."""
        for row in _rows():
            narrow = row["file_key"].replace(".webp", "-960.webp")
            assert (self.MEDIA_DIR / narrow).is_file(), narrow


@pytest.mark.django_db
class TestLoadingIt:
    def test_the_seed_reaches_the_database_with_its_credits(self) -> None:
        from django.core.management import call_command

        call_command("seed", verbosity=0)

        rows = Media.objects.all()
        assert rows.count() == len(_rows())
        for media in rows:
            assert media.attribution, media.file_key
            assert media.license_code, media.file_key

    def test_a_market_carries_a_gallery(self) -> None:
        """ADR 0018's point, end to end: the landing page hero is a row in a
        table, so opening a market is data rather than a deployment."""
        from django.core.management import call_command

        call_command("seed", verbosity=0)
        assert Media.objects.filter(owner_type=MediaOwnerType.MARKET).exists()

    def test_a_second_run_updates_rather_than_duplicating(self) -> None:
        from django.core.management import call_command

        call_command("seed", verbosity=0)
        first = Media.objects.count()
        call_command("seed", verbosity=0)
        assert Media.objects.count() == first
