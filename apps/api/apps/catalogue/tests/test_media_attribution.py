"""A licensed image must carry its credit — `media`, §7.3, §35.7.

This is a legal constraint wearing a schema's clothes, and it is worth being
explicit about why it is enforced in PostgreSQL rather than in a serializer or
a review checklist.

Attribution is a **condition** of CC BY. An uncredited CC BY photograph on a
commercial tourism page is a licence breach, and it renders identically to a
correctly credited one: same pixels, same layout, same Lighthouse score, no
error anywhere. Nothing in the system notices, and the party who does notice
is not a test runner.

So the rule is a `CheckConstraint`, and these tests exercise it against a real
database rather than asserting that a Python function returns False. The
constraint is the guarantee; a validator in front of it is a convenience.

`license_code = ""` means own work and is the only exemption. It is deliberate
that the exemption is a *positive* claim about provenance rather than a
default that a forgotten field falls into — which is why the licensed cases
below check both halves, credit and licence URL, separately.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.catalogue.models import Media, MediaOwnerType
from apps.catalogue.tests.factories import make_destination

pytestmark = pytest.mark.django_db

COMMONS = "https://commons.wikimedia.org/wiki/File:Stone_Town.jpg"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"


def _media(**overrides: object) -> Media:
    destination = make_destination()
    values: dict[str, object] = {
        "owner_type": MediaOwnerType.DESTINATION,
        "owner_id": destination.id,
        "file_key": "abc123.webp",
        "alt_text": "Carved doors on a coral-stone street.",
        "width": 1600,
        "height": 900,
    }
    values.update(overrides)
    return Media.objects.create(**values)


class TestTheDatabaseRefusesAnUncreditedLicensedImage:
    def test_a_licence_without_a_credit_is_refused(self) -> None:
        with (
            pytest.raises(IntegrityError, match="media_licensed_rows_carry_attribution"),
            transaction.atomic(),
        ):
            _media(license_code="CC BY 4.0", license_url=CC_BY, attribution="")

    def test_a_licence_without_a_licence_url_is_refused(self) -> None:
        """Naming a licence is not identifying it. "CC BY 4.0" is four
        characters and a guess; the deed URL is what the condition asks for."""
        with (
            pytest.raises(IntegrityError, match="media_licensed_rows_carry_attribution"),
            transaction.atomic(),
        ):
            _media(license_code="CC BY 4.0", license_url="", attribution="A. Photographer")

    def test_a_fully_credited_licensed_image_is_accepted(self) -> None:
        row = _media(
            license_code="CC BY 4.0",
            license_url=CC_BY,
            attribution="A. Photographer",
            source_url=COMMONS,
        )
        assert row.pk is not None

    def test_own_work_needs_no_credit(self) -> None:
        """The only exemption, and it is a claim rather than an oversight:
        `license_code = ""` asserts the Platform owns the picture."""
        row = _media()
        assert row.license_code == ""
        assert row.attribution == ""

    def test_the_credit_cannot_be_removed_after_the_fact(self) -> None:
        """The interesting direction. A row can be created correctly and then
        edited into breach, and an INSERT-only guard would miss it — which is
        how a bulk `update()` clearing a column becomes a licence problem."""
        row = _media(license_code="CC0", license_url=CC_BY, attribution="A. Photographer")
        with (
            pytest.raises(IntegrityError, match="media_licensed_rows_carry_attribution"),
            transaction.atomic(),
        ):
            Media.objects.filter(pk=row.pk).update(attribution="")


class TestTheCreditReachesTheClient:
    def test_the_payload_carries_provenance_on_every_image(self) -> None:
        """Always present, never conditional.

        A client that rendered a credit only when the field happened to be in
        the payload would fail open, and failing open here is the breach. The
        shape is fixed; own work is `license_code: ""`.
        """
        from apps.catalogue.selectors import to_media_dto

        dto = to_media_dto(
            _media(
                license_code="CC BY 4.0",
                license_url=CC_BY,
                attribution="A. Photographer",
                source_url=COMMONS,
            )
        )
        assert dto is not None
        assert dto.attribution == "A. Photographer"
        assert dto.license_code == "CC BY 4.0"
        assert dto.license_url == CC_BY
        assert dto.source_url == COMMONS

    def test_own_work_still_carries_the_fields_empty(self) -> None:
        from apps.catalogue.selectors import to_media_dto

        dto = to_media_dto(_media())
        assert dto is not None
        assert (dto.attribution, dto.license_code, dto.license_url) == ("", "", "")
