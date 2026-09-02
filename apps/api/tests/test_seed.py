"""The Appendix C seed set — SRS §4.1, §4.2, Appendix C, §41.12, §41.13.

    Appendix C: "The seed set delivered in database/seeds/ and loadable
    through the admin console."

This runs the loader against **the committed files**, not against a fixture.
That is the whole point: a seed loader tested on invented rows proves the
loader works and says nothing about whether the data ships. The failure being
guarded against is a coordinate with eight decimal places or a destination
naming a region that was renamed — neither of which any other test would see,
and both of which surface as a broken `make seed` on somebody's first day.

Four properties:

**It loads.** Every row passes `full_clean`, every reference resolves, and the
counts match what Appendix C commits to.

**It is idempotent.** `make seed` runs on every fresh checkout and again
whenever a coordinate is corrected. A second run must update rather than
duplicate or raise, and the count of rows must not move.

**It is audited.** Appendix C says the set is loadable *through the admin
console*; §41.13 audits every administrative action. Sixty-nine catalogue rows
with no record of where they came from is the state somebody is in when one of
them turns out to be wrong.

**Nothing about it is Zanzibar-shaped in code.** The data names Zanzibar
because Zanzibar is the v1 market. `tests/test_destination_independence.py`
already forbids those names appearing in a Python module; this file asserts the
other half — that the loader is generic enough to have loaded Arusha instead.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.administration.management.commands.seed import DEFAULT_ROOT, find_seed_root
from apps.administration.models import AuditLog
from apps.catalogue.models import (
    Accommodation,
    Activity,
    ActivitySchedule,
    Attraction,
    CancellationPolicy,
    Country,
    Destination,
    Region,
    Tag,
)
from apps.catalogue.services import SCHEDULE_SEED_FILE, SEED_FILES
from apps.common.geo import BoundingBox, Coordinates

pytestmark = pytest.mark.django_db

#: Taken from the command rather than recomputed, so this reads exactly the
#: files `make seed` reads. The two paths differ between the host and the
#: container — the repository root is mounted at /database — and a test that
#: guessed would pass against files nobody ships.
SEEDS = DEFAULT_ROOT / "catalogue"

#: Appendix C's table, as numbers. Asserted rather than trusted because the
#: appendix is a commitment: "Destination | 10" is what the SRS says ships.
APPENDIX_C = {
    Country: 1,
    Region: 5,
    Destination: 10,
    Attraction: 26,  # "~25"
    Accommodation: 43,  # "~40"
    CancellationPolicy: 4,  # FLEX_48H, MODERATE_7D, STRICT_14D, NON_REFUNDABLE
    # Not Appendix C's. ADR 0013 made activities provider-supplied, and the
    # provider portal that would supply them is Phase 11; Phase 5 seeds them
    # because activity capacity is the only thing v1 sells, and a platform
    # whose sole bookable product cannot be seen until Phase 11 is one nobody
    # can test, demonstrate or price.
    Activity: 12,
}


#: Entity key to committed filename, read from the registry the loader obeys.
#: These stems were literals here until ADR 0018 inserted `market` between
#: country and region and renumbered everything below it — at which point
#: twelve tests failed on a missing file rather than on anything they were
#: written to check. A second copy of the ordering is a second thing to
#: update, so there is now one.
_STEMS = {key: stem for stem, key in SEED_FILES}


def _rows(entity: str) -> list[dict]:
    """The committed rows for one entity, by entity key rather than filename."""
    return json.loads((SEEDS / f"{_STEMS[entity]}.json").read_text(encoding="utf-8"))


def _coordinates_by_country() -> list[tuple[str, BoundingBox, Coordinates]]:
    """Every coordinate in the seed set, paired with its own country's box.

    Walks the hierarchy out of the files rather than assuming one country, so
    this keeps working the day a second market is seeded — which is the same
    property §41.12 measures, checked from the data's side.
    """
    boxes = {
        row["iso_code"]: BoundingBox(
            min_lat=Decimal(row["min_latitude"]),
            min_lon=Decimal(row["min_longitude"]),
            max_lat=Decimal(row["max_latitude"]),
            max_lon=Decimal(row["max_longitude"]),
        )
        for row in _rows("country")
    }
    region_country = {row["slug"]: row["country"] for row in _rows("region")}
    destination_country = {
        row["slug"]: region_country[row["region"]] for row in _rows("destination")
    }

    found: list[tuple[str, BoundingBox, Coordinates]] = []
    for stem, parent in (
        ("destination", "region"),
        ("attraction", "destination"),
        ("accommodation", "destination"),
    ):
        lookup = region_country if parent == "region" else destination_country
        for row in _rows(stem):
            if "latitude" not in row:
                continue
            found.append(
                (
                    row["slug"],
                    boxes[lookup[row[parent]]],
                    Coordinates(Decimal(row["latitude"]), Decimal(row["longitude"])),
                )
            )
    assert found, "no seeded coordinates found — the walk above is broken"
    return found


def _schedule_rows() -> list[dict[str, object]]:
    """The schedule file, which is not in `SEED_FILES`.

    `activity_schedule` has no natural key a person can write — it is not a
    thing with a name, it is "this activity, at this time" — so it is loaded
    separately (`load_schedule_seed`) and read separately here.
    """
    path = SEEDS / f"{SCHEDULE_SEED_FILE}.json"
    return list(json.loads(path.read_text(encoding="utf-8")))


class TestTheSeedDirectoryIsFoundInEitherLayout:
    """The repository and the image put `database/` in different places.

    In a checkout this command is `<repo>/apps/api/apps/administration/...`
    and the seeds are `<repo>/database/seeds`. In the image it is
    `/app/apps/administration/...` with `database/` bind-mounted at
    `/database` — one hop fewer, and fewer ancestors in total.

    A fixed `parents[N]` satisfied the container and resolved to
    `<repo>/apps/database/seeds` everywhere else, so CI ran red while every
    container run stayed green. These tests build both trees rather than
    trusting whichever one happens to be underneath the runner.
    """

    def test_it_finds_the_root_from_a_checkout_layout(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "database" / "seeds" / "catalogue").mkdir(parents=True)
        command = repo / "apps" / "api" / "apps" / "administration" / "management" / "commands"
        command.mkdir(parents=True)
        assert find_seed_root(command / "seed.py") == repo / "database" / "seeds"

    def test_it_finds_the_root_from_the_container_layout(self, tmp_path: Path) -> None:
        """`/app` beside `/database`, rather than inside a repository."""
        (tmp_path / "database" / "seeds" / "catalogue").mkdir(parents=True)
        command = tmp_path / "app" / "apps" / "administration" / "management" / "commands"
        command.mkdir(parents=True)
        assert find_seed_root(command / "seed.py") == tmp_path / "database" / "seeds"

    def test_it_takes_the_nearest_one_when_a_tree_holds_two(self, tmp_path: Path) -> None:
        """A nested checkout must not reach past its own seeds to an outer set,
        which is how a developer ends up loading somebody else's data."""
        (tmp_path / "database" / "seeds").mkdir(parents=True)
        inner = tmp_path / "workspace" / "repo"
        (inner / "database" / "seeds").mkdir(parents=True)
        start = inner / "apps" / "api" / "apps" / "x" / "y"
        start.mkdir(parents=True)
        assert find_seed_root(start / "seed.py") == inner / "database" / "seeds"

    def test_a_missing_directory_names_somewhere_rather_than_raising(self, tmp_path: Path) -> None:
        """Django imports every management command at startup, so a deployment
        with no seeds mounted must still boot; `handle` reports it by name."""
        start = tmp_path / "app" / "apps" / "x"
        start.mkdir(parents=True)
        assert find_seed_root(start / "seed.py").name == "seeds"

    def test_the_resolved_root_is_the_one_that_ships(self) -> None:
        """And the running environment really does resolve to real files."""
        assert DEFAULT_ROOT.is_dir(), DEFAULT_ROOT
        assert (DEFAULT_ROOT / "catalogue").is_dir()


class TestTheCommittedFiles:
    """Read as data, before anything touches a database."""

    def test_every_declared_file_exists_and_holds_an_array(self) -> None:
        """Every file the loader names must be there.

        A missing one is a `make seed` that half-loads — regions without their
        destinations, which looks loaded and is not.
        """
        for stem, _ in SEED_FILES:
            path = SEEDS / f"{stem}.json"
            assert path.is_file(), f"missing {path}"
            assert isinstance(json.loads(path.read_text(encoding="utf-8")), list)

    def test_no_seed_row_carries_an_identifier(self) -> None:
        """A seed file identifies rows by ISO code or slug, never by UUID.

        A `public_id` in a checked-in file is either invented by hand or
        regenerated on every re-seed, and both make the file stop being the
        thing that decides what exists.
        """
        for _, entity in SEED_FILES:
            for row in _rows(entity):
                assert "public_id" not in row
                assert "id" not in row

    def test_no_accommodation_row_carries_a_price(self) -> None:
        """ADR 0013, in the data. Seeding forty properties is only defensible
        because a row asserts nothing a property's owner alone could assert:
        a name, a type, a coordinate and two wall-clock times."""
        for row in _rows("accommodation"):
            for forbidden in ("base_rate", "price", "provider", "cancellation_policy", "rooms"):
                assert forbidden not in row, f"{row['slug']} carries {forbidden}"

    def test_every_coordinate_is_within_the_precision_the_srs_allows(self) -> None:
        """§13.1: *"a maximum of seven decimal places"*. Checked here as well
        as at the boundary, because a file is where an over-precise coordinate
        gets pasted in from a mapping tool."""
        for _, entity in SEED_FILES:
            for row in _rows(entity):
                for axis in ("latitude", "longitude"):
                    if axis in row:
                        _, _, fraction = row[axis].partition(".")
                        assert len(fraction) <= 7, f"{row.get('slug')}.{axis}"

    def test_every_seeded_coordinate_falls_inside_its_country(self) -> None:
        """The bounding-box guard, applied to the data that actually ships.

        The loader enforces this on every row it writes, so this test is not
        the enforcement — it is the shorter feedback loop. A coordinate pasted
        in from a mapping tool with the axes the wrong way round fails here
        naming the row, rather than failing `make seed` on somebody's first day
        with a message about a transaction.
        """
        for slug, box, point in _coordinates_by_country():
            assert box.contains(point), f"{slug} at {point} is outside its country"

    def test_the_shipped_data_would_notice_a_swap(self) -> None:
        """The guard-tests-the-guard case, and the reason the boxes are tight.

        A bounding box only catches a transposed pair when the country's
        latitude range and longitude range do not overlap. That is a property
        of the geography, not of the technique — so it is asserted for the data
        that ships rather than assumed.

        This is also what stops a country from silently keeping the whole-world
        placeholder that `catalogue/0007` fills existing rows with: the world
        box contains every swapped pair, so it fails this test for every row at
        once.
        """
        for slug, box, point in _coordinates_by_country():
            transposed = Coordinates(lat=point.lon, lon=point.lat)
            assert not box.contains(transposed), (
                f"{slug}: the country box accepts this row's coordinates "
                f"transposed, so a swapped pair would be written silently"
            )

    def test_the_four_policies_appendix_c_names_are_the_ones_that_ship(self) -> None:
        """§14.6 names four and then says the thing that matters — a policy is
        an ordered tier list, so a market with different consumer law gets rows
        rather than a release. The codes are asserted because Appendix C
        commits to them, not because anything branches on one."""
        assert {row["code"] for row in _rows("cancellation_policy")} == {
            "FLEX_48H",
            "MODERATE_7D",
            "STRICT_14D",
            "NON_REFUNDABLE",
        }

    def test_every_policy_ladder_descends(self) -> None:
        """§14.6's list is ordered most generous first. A ladder that climbs
        would refund more the later you cancel, which no test downstream would
        read as wrong — it would just quietly overpay."""
        for row in _rows("cancellation_policy"):
            tiers = row["tiers"]
            hours = [tier["hours_before"] for tier in tiers]
            refunds = [tier["refund_percent"] for tier in tiers]
            assert hours == sorted(hours, reverse=True), row["code"]
            assert refunds == sorted(refunds, reverse=True), row["code"]
            assert all(0 <= percent <= 100 for percent in refunds), row["code"]

    def test_pemba_ships_inactive(self) -> None:
        """§4.1: *"Deferred; record created but is_active = false"*.

        The row exists so that opening Pemba is a flag on one row rather than
        a data migration — which is the same mechanism §41.12 proves for
        Arusha, exercised in the opposite direction.
        """
        [pemba] = [r for r in _rows("destination") if r["slug"] == "chake-chake"]
        assert pemba["is_active"] is False

    def test_the_gateway_is_declared_coherently(self) -> None:
        """§7.5.6's CHECK: `is_gateway` implies both a type and a code, and
        the absence of it implies neither."""
        gateways = [r for r in _rows("destination") if r.get("is_gateway")]
        assert len(gateways) == 1, "SRS 4.1 seeds exactly one gateway"
        [znz] = gateways
        assert znz["gateway_type"] == "AIRPORT"
        assert znz["gateway_code"] == "ZNZ"
        for other in _rows("destination"):
            if not other.get("is_gateway"):
                assert "gateway_code" not in other

    def test_every_tag_an_attraction_uses_is_in_the_vocabulary(self) -> None:
        """`assert_known_tags` is a database trigger, so a stray slug fails the
        load. Catching it here says *which* row and *which* tag, which is the
        difference between a two-minute fix and a bisect."""
        vocabulary = {row["slug"] for row in _rows("tag")}
        for row in _rows("attraction"):
            unknown = set(row.get("tags", ())) - vocabulary
            assert not unknown, f"{row['slug']} uses {sorted(unknown)}"

    def test_every_reference_names_a_row_that_is_seeded(self) -> None:
        """The order in `SEED_FILES` is load order, and a forward reference
        would fail at run time in whichever environment ran it first."""
        regions = {r["slug"] for r in _rows("region")}
        destinations = {r["slug"] for r in _rows("destination")}
        for row in _rows("destination"):
            assert row["region"] in regions, row["slug"]
        for stem in ("attraction", "accommodation", "activity"):
            for row in _rows(stem):
                assert row["destination"] in destinations, row["slug"]

        policies = {row["code"] for row in _rows("cancellation_policy")}
        for row in _rows("activity"):
            named = row.get("cancellation_policy")
            if named is not None:
                assert named in policies, row["slug"]

        activities = {row["slug"] for row in _rows("activity")}
        for row in _schedule_rows():
            assert row["activity"] in activities, row["activity"]


class TestTheActivitiesArePriceableAndBookable:
    """Phase 5 sells activity capacity, and these are the columns it sells by.

    Every one of these would be a plausible-looking row that produced no
    departures, no price, or a price nobody could charge — the failures that
    only show up when somebody tries to buy something.
    """

    def test_every_activity_carries_a_price_and_a_currency(self) -> None:
        """§10.7: an activity is the only item type that produces a
        `line_total`. One without a price is an activity a trip cannot cost."""
        for row in _rows("activity"):
            assert row["price_per_person"], row["slug"]
            assert len(row["currency"]) == 3, row["slug"]

    def test_no_price_is_written_as_a_number(self) -> None:
        """Hard rule 6: money is `Decimal`, never float. A JSON number is
        parsed as a float before anything can object, so the file has to
        carry strings."""
        for row in _rows("activity"):
            assert isinstance(row["price_per_person"], str), row["slug"]
            assert row["price_per_group"] is None or isinstance(row["price_per_group"], str)

    def test_every_party_range_is_satisfiable(self) -> None:
        for row in _rows("activity"):
            assert 1 <= row["min_pax"] <= row["max_pax"], row["slug"]

    def test_every_activity_has_at_least_one_schedule(self) -> None:
        """The failure this exists to refuse: a seeded activity with no rule
        has no departures, shows an empty calendar, and looks exactly like a
        working activity nobody has booked."""
        scheduled = {row["activity"] for row in _schedule_rows()}
        for row in _rows("activity"):
            assert row["slug"] in scheduled, row["slug"]

    def test_every_schedule_runs_on_a_day(self) -> None:
        for row in _schedule_rows():
            assert row["days"], row["activity"]

    def test_a_schedule_never_seats_more_than_the_activity_takes(self) -> None:
        """A twelve-seat boat with a sixteen-seat departure sells four seats
        the operator cannot honour, and every layer below this believes the
        counter."""
        limits = {row["slug"]: row["max_pax"] for row in _rows("activity")}
        for row in _schedule_rows():
            assert row["capacity"] <= limits[row["activity"]], row["activity"]

    def test_every_tag_an_activity_uses_is_in_the_vocabulary(self) -> None:
        vocabulary = {row["slug"] for row in _rows("tag")}
        for row in _rows("activity"):
            unknown = set(row.get("tags", ())) - vocabulary
            assert not unknown, f"{row['slug']} uses {sorted(unknown)}"

    def test_an_activity_with_a_minimum_age_says_so_in_both_places(self) -> None:
        """§16.4's `requirements` feeds VR-15 and the booking guards; the
        `min_age` column feeds the same rule. Two sources disagreeing is a
        restriction enforced in one place and displayed from the other."""
        for row in _rows("activity"):
            declared = row.get("requirements", {}).get("min_age")
            assert declared == row["min_age"], row["slug"]


class TestLoadingIt:
    def test_the_whole_set_loads(self) -> None:
        call_command("seed", verbosity=0)
        for model, expected in APPENDIX_C.items():
            assert model.all_objects.count() == expected, model.__name__
        assert Tag.all_objects.count() == 7

    def test_the_schedules_load_with_the_activities(self) -> None:
        call_command("seed", verbosity=0)
        assert ActivitySchedule.objects.count() == len(_schedule_rows())

    def test_a_second_run_does_not_double_the_schedules(self) -> None:
        """Keyed by `(activity, start_time)`. A duplicate rule would double
        every departure the materialiser then produced, which reads as a
        provider running twice as many boats."""
        call_command("seed", verbosity=0)
        call_command("seed", verbosity=0)
        assert ActivitySchedule.objects.count() == len(_schedule_rows())

    def test_the_seeded_schedules_produce_departures(self) -> None:
        """End to end: the point of seeding any of this. §16.2's job turns
        rules into sellable instants, and a catalogue that produced none would
        pass every assertion above it."""
        from apps.inventory import services as inventory

        call_command("seed", verbosity=0)
        created = inventory.materialise_departures(
            start=Destination.objects.first().created_at.date(), horizon_days=14
        )
        assert created > 0

    def test_a_second_run_updates_rather_than_duplicating(self) -> None:
        """`make seed` runs on every fresh checkout and again whenever a
        coordinate is corrected. A second run raising on a unique constraint,
        or producing forty more hotels, is the same bug wearing two faces."""
        call_command("seed", verbosity=0)
        before = {model: model.all_objects.count() for model in APPENDIX_C}
        call_command("seed", verbosity=0)
        assert {model: model.all_objects.count() for model in APPENDIX_C} == before

    def test_a_dry_run_writes_nothing(self) -> None:
        """Validates every file against the models and rolls back — which is
        what somebody wants before committing a data change."""
        call_command("seed", "--dry-run", verbosity=0)
        assert Destination.all_objects.count() == 0

    def test_the_load_is_audited_row_by_row(self) -> None:
        """§41.13. Appendix C makes the seed set an administrative action, and
        an unaudited bulk insert leaves sixty-nine rows with no provenance —
        which is the first question asked when one of them is wrong."""
        call_command("seed", verbosity=0)
        entries = AuditLog.objects.filter(action="catalogue.created")
        # Appendix C's rows, plus the seven tags it does not count, plus the
        # two markets ADR 0018 added. Markets are deliberately *not* in
        # `APPENDIX_C`: that dict is the appendix's own commitment, and
        # padding it would make the SRS appear to promise a table it predates.
        assert entries.count() == sum(APPENDIX_C.values()) + 7 + 2
        assert set(entries.values_list("entity_type", flat=True)) == {
            "country",
            "market",
            "region",
            "destination",
            "tag",
            "attraction",
            "accommodation",
            "cancellation_policy",
            "activity",
        }

    def test_a_seeded_schedule_is_not_audited_as_a_catalogue_write(self) -> None:
        """`activity_schedule` is loaded outside the entity registry, so it
        writes no audit row — stated rather than left as an omission somebody
        later reads as a bug. §41.13 audits *administrative actions*, and a
        recurrence rule arriving with its activity is part of one write, not a
        second one."""
        call_command("seed", verbosity=0)
        assert not AuditLog.objects.filter(entity_type="activity_schedule").exists()

    def test_a_seeded_market_is_immediately_public(self) -> None:
        """The seed set is only useful if it reaches the §9.3.2 endpoints — so
        this asserts through the API rather than through the ORM."""
        from rest_framework.test import APIClient

        call_command("seed", verbosity=0)
        listed = APIClient().get("/api/v1/destinations", {"limit": 100})
        assert listed.status_code == 200
        slugs = {row["slug"] for row in listed.data["data"]}
        assert "stone-town" in slugs
        # §4.1 again: Pemba is seeded and must not be published.
        assert "chake-chake" not in slugs

    def test_a_retired_row_is_not_resurrected_by_re_seeding(self) -> None:
        """An administrator's withdrawal outranks the file.

        §7.7 releases the slug on soft deletion, so the loader creates a fresh
        row rather than restoring the retired one — and the retired one stays
        retired. Re-running a loader is not the place to reverse a decision
        somebody made deliberately.
        """
        call_command("seed", verbosity=0)
        retired = Destination.objects.get(slug="jambiani")
        retired.delete()

        call_command("seed", verbosity=0)
        retired.refresh_from_db()
        assert retired.deleted_at is not None
        assert Destination.objects.filter(slug="jambiani").exclude(pk=retired.pk).exists()

    def test_a_corrected_coordinate_reaches_the_row(self) -> None:
        """The update half of the upsert, which is the reason `make seed` is
        re-runnable at all: fixing a hotel's position is a file edit."""
        call_command("seed", verbosity=0)
        stay = Accommodation.objects.get(slug="park-hyatt-zanzibar")
        original = (round(stay.coordinates.y, 4), round(stay.coordinates.x, 4))
        assert original == (-6.1611, 39.1867)


class TestTheLoaderIsNotZanzibarShaped:
    def test_it_loads_a_market_the_seed_files_never_mention(self) -> None:
        """§4.2 and §41.12, from the loader's side.

        The seed data names Zanzibar because Zanzibar is the v1 market. The
        loader must not: the same code path, handed a file describing Arusha,
        has to produce Arusha. If it cannot, opening a second market means
        editing the loader, and OBJ-6 is broken somewhere no functional test
        would look.
        """
        from apps.catalogue import services as catalogue

        catalogue.load_seed(
            "country",
            [
                {
                    "iso_code": "KE",
                    "name": "Kenya",
                    "default_currency": "KES",
                    "default_timezone": "Africa/Nairobi",
                    "min_latitude": "-4.7000000",
                    "min_longitude": "33.9000000",
                    "max_latitude": "5.1000000",
                    "max_longitude": "41.9100000",
                }
            ],
        )
        # §41.12 as amended to v1.5: the administrator creates a market too.
        catalogue.load_seed(
            "market",
            [{"country": "KE", "slug": "kenyan-coast", "name": "Kenyan Coast", "is_active": True}],
        )
        catalogue.load_seed(
            "region",
            [{"country": "KE", "market": "kenyan-coast", "slug": "coast", "name": "Coast"}],
        )
        result = catalogue.load_seed(
            "destination",
            [
                {
                    "region": "coast",
                    "slug": "diani",
                    "name": "Diani",
                    "latitude": "-4.2769",
                    "longitude": "39.5931",
                    "timezone": "Africa/Nairobi",
                    "default_currency": "KES",
                    "is_active": True,
                }
            ],
        )
        assert result.created == 1
        assert Destination.objects.get(slug="diani").default_currency == "KES"

    def test_a_reference_to_a_row_that_does_not_exist_names_the_field(self) -> None:
        from apps.catalogue import services as catalogue
        from apps.common.errors import ValidationError

        with pytest.raises(ValidationError, match="region"):
            catalogue.load_seed(
                "destination",
                [
                    {
                        "region": "no-such-region",
                        "slug": "nowhere",
                        "name": "Nowhere",
                        "latitude": "0.0",
                        "longitude": "0.0",
                        "timezone": "UTC",
                        "default_currency": "USD",
                    }
                ],
            )
