"""The full-text columns — SRS §7.6, §9.3.2, §24.7.

`domain.search` already owns turning a tourist's typing into something safe to
hand PostgreSQL. This file is about the other half: that the thing being
searched is maintained by the database and cannot drift.

The distinction matters more than it looks. A trigger, or a `save()` override,
or a Celery task would each leave a window — a `COPY`, a `QuerySet.update()`, a
data migration, a writer that disabled triggers — in which a row exists and its
index entry does not. A row that is present, correct and unfindable is a bug
with no symptom except an absence, and absences are not reported.

So the assertions here are deliberately made through the paths that bypass
application code entirely.
"""

from __future__ import annotations

import pytest
from django.contrib.postgres.search import SearchQuery
from django.db import models

from apps.catalogue.models import Accommodation, Activity, Attraction, Destination
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_attraction,
    make_destination,
)

WEBSEARCH = "websearch"


def _matches(model: type[models.Model], text: str) -> int:
    return model.objects.filter(  # type: ignore[attr-defined]
        search_vector=SearchQuery(text, config="english", search_type=WEBSEARCH)
    ).count()


class TestTheColumnIsGeneratedNotWritten:
    def test_every_searched_table_carries_the_column(self) -> None:
        for model in (Destination, Attraction, Activity, Accommodation):
            assert model._meta.get_field("search_vector") is not None

    def test_it_is_a_generated_stored_column(self) -> None:
        """Not a trigger, not a `save()` override, not a background task."""
        for model in (Destination, Attraction, Activity, Accommodation):
            field = model._meta.get_field("search_vector")
            assert isinstance(field, models.GeneratedField)
            assert field.db_persist is True

    def test_no_application_code_assigns_to_it(self) -> None:
        import inspect

        from apps.catalogue import models as catalogue_models

        source = inspect.getsource(catalogue_models)
        assert "search_vector =" in source  # the declaration
        assert "self.search_vector" not in source


@pytest.mark.django_db
class TestTheDatabaseMaintainsIt:
    def test_a_new_row_is_searchable_immediately(self) -> None:
        make_destination(name="Whale Bay", description="A quiet cove with black sand")
        assert _matches(Destination, "whale bay") == 1

    def test_the_index_follows_a_bulk_update(self) -> None:
        """`QuerySet.update()` never calls `save()`. A generated column does
        not care; anything written in Python would."""
        destination = make_destination(name="Whale Bay")
        Destination.objects.filter(pk=destination.pk).update(name="Dolphin Point")
        assert _matches(Destination, "whale") == 0
        assert _matches(Destination, "dolphin") == 1

    def test_the_description_is_searched_as_well_as_the_name(self) -> None:
        make_destination(name="Whale Bay", description="Famous for its lighthouse")
        assert _matches(Destination, "lighthouse") == 1

    def test_a_null_summary_does_not_erase_the_rest(self) -> None:
        """`coalesce` inside the expression: one null column must not make the
        whole vector null and the row unfindable."""
        make_destination(name="Whale Bay", summary=None, description="")
        assert _matches(Destination, "whale") == 1

    def test_stemming_is_applied(self) -> None:
        """The reason the dictionary is `english` and not `simple`: a tourist
        typing "beaches" means the row that says "beach"."""
        make_destination(name="Long Beach")
        assert _matches(Destination, "beaches") == 1

    def test_all_four_tables_are_searched_the_same_way(self) -> None:
        destination = make_destination(name="Whale Bay")
        make_attraction(destination=destination, name="The Old Lighthouse", slug="old-lighthouse")
        make_activity(
            destination=destination, name="Lighthouse Kayak Tour", slug="lighthouse-kayak"
        )
        make_accommodation(
            destination=destination, name="Lighthouse Lodge", slug="lighthouse-lodge"
        )
        assert _matches(Attraction, "lighthouse") == 1
        assert _matches(Activity, "lighthouse") == 1
        assert _matches(Accommodation, "lighthouse") == 1


@pytest.mark.django_db
class TestArbitraryTypingDoesNotReachTheDatabaseUnsafely:
    """§7.6 with a public, unauthenticated endpoint on the other end."""

    @pytest.mark.parametrize(
        "typed",
        [
            "fish & chips",
            "beach | ocean",
            "!!!",
            "stone (town",
            "a:b:c",
            "<>",
            "beach & ",
        ],
    )
    def test_operator_characters_do_not_raise(self, typed: str) -> None:
        """`to_tsquery` raises a database error on any of these, which on a
        public endpoint is both a 500 and a small denial of service.
        `websearch_to_tsquery` never does."""
        assert _matches(Destination, typed) >= 0

    def test_a_quoted_phrase_is_honoured(self) -> None:
        make_destination(name="Stone Town", description="The old quarter")
        assert _matches(Destination, '"stone town"') == 1

    def test_a_phrase_in_the_wrong_order_does_not_match(self) -> None:
        make_destination(name="Stone Town")
        assert _matches(Destination, '"town stone"') == 0

    def test_two_words_are_an_and_not_an_or(self) -> None:
        make_destination(name="Stone Town")
        assert _matches(Destination, "stone lighthouse") == 0
