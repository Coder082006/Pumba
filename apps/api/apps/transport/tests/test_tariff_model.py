"""The tariff schema — SRS §12.4, ADR 0023.

§12.4 gives `transfer_corridor` and `transfer_tariff` a field name and a
one-line meaning each and stops. There is no §7.5 specification, no §7.3 ERD
box, no §7.4 relationship row and no §7.6 index. Every structural claim these
tables make is therefore an invention, and this file is where the inventions
are held to account.

**The exclusion constraints are the point.** §12.4's ladder says "first match
wins". Two live corridors for the same (origin, target, class) on the same day
would make the winner an accident of primary key order — a coin flip on a
price, reproducible until somebody reindexed. A CHECK cannot see across rows
and an application check is a race, so the constraint is in the database and is
asserted here against a real one.

Nothing in this file constructs a `destination` or a `region`. Those are
`catalogue` rows and `transport` may not see them; the columns are bare
integers by design (ADR 0012), and using plausible integers here is not
laziness, it is the contract.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction

from apps.transport.models import TariffScope, TransferCorridor, TransferTariff, VehicleClass

pytestmark = pytest.mark.django_db

JANUARY = dt.date(2027, 1, 1)
JULY = dt.date(2027, 7, 1)


def _class(code: str = "STANDARD", **overrides: object) -> VehicleClass:
    values: dict[str, object] = {
        "code": code,
        "name": code.title(),
        "seats": 4,
        "luggage_capacity": 3,
    }
    values.update(overrides)
    return VehicleClass.objects.create(**values)


def _corridor(**overrides: object) -> TransferCorridor:
    values: dict[str, object] = {
        "origin_destination_id": 1,
        "target_destination_id": 2,
        "vehicle_class": overrides.pop("vehicle_class", None) or _class(),
        "fixed_price": Decimal("38.00"),
        "currency": "USD",
        "valid_from": JANUARY,
    }
    values.update(overrides)
    return TransferCorridor.objects.create(**values)


def _tariff(**overrides: object) -> TransferTariff:
    values: dict[str, object] = {
        "scope": TariffScope.REGION,
        "region_id": 7,
        "vehicle_class": overrides.pop("vehicle_class", None) or _class(),
        "base_fare": Decimal("12.00"),
        "per_km_rate": Decimal("0.4000"),
        "currency": "USD",
        "valid_from": JANUARY,
    }
    values.update(overrides)
    return TransferTariff.objects.create(**values)


class TestOneCorridorAnswersAtATime:
    def test_two_live_corridors_for_one_triple_are_refused(self) -> None:
        """The assertion the whole constraint exists for."""
        klass = _class()
        _corridor(vehicle_class=klass)
        with pytest.raises(IntegrityError, match="no_overlapping_window"):
            _corridor(vehicle_class=klass, fixed_price=Decimal("99.00"))

    def test_an_overlapping_window_is_refused_even_when_the_dates_differ(self) -> None:
        klass = _class()
        _corridor(vehicle_class=klass, valid_from=JANUARY, valid_to=dt.date(2027, 12, 31))
        with pytest.raises(IntegrityError):
            _corridor(vehicle_class=klass, valid_from=JULY)

    def test_an_adjacent_window_is_allowed(self) -> None:
        """Half-open, deliberately. A tariff valid *to* the 1st of July and its
        successor valid *from* the 1st of July are how one rate replaces
        another, and a closed upper bound would reject the ordinary case."""
        klass = _class()
        _corridor(vehicle_class=klass, valid_from=JANUARY, valid_to=JULY)
        later = _corridor(vehicle_class=klass, valid_from=JULY, fixed_price=Decimal("42.00"))
        assert later.pk is not None

    def test_a_second_vehicle_class_on_the_same_pair_is_allowed(self) -> None:
        """The control. Without it every assertion above would also pass on a
        constraint that simply forbade a second corridor of any kind."""
        _corridor(vehicle_class=_class("STANDARD"))
        van = _corridor(vehicle_class=_class("VAN", seats=7, luggage_capacity=6))
        assert van.pk is not None

    def test_a_withdrawn_corridor_frees_its_window(self) -> None:
        """`is_active = false` is how an administrator retires a price without
        deleting the row §27.11 audits. It must not keep blocking the
        replacement."""
        klass = _class()
        first = _corridor(vehicle_class=klass)
        first.is_active = False
        first.save(update_fields=["is_active"])
        assert _corridor(vehicle_class=klass, fixed_price=Decimal("50.00")).pk is not None

    def test_a_soft_deleted_corridor_frees_its_window(self) -> None:
        klass = _class()
        _corridor(vehicle_class=klass).delete()
        assert _corridor(vehicle_class=klass).pk is not None


class TestACorridorIsCoherent:
    def test_a_corridor_from_a_place_to_itself_is_refused(self) -> None:
        """§12.2: "A leg whose origin and target resolve to the same coordinate
        is rejected at validation." A corridor is that leg priced in advance."""
        with pytest.raises(IntegrityError, match="endpoints_differ"):
            _corridor(origin_destination_id=5, target_destination_id=5)

    def test_a_free_corridor_is_refused(self) -> None:
        """Zero is a price somebody typed, not a price somebody meant. A free
        transfer is a commercial decision that belongs in a discount, not in
        the fare table."""
        with pytest.raises(IntegrityError, match="price_positive"):
            _corridor(fixed_price=Decimal("0.00"))

    def test_a_window_that_ends_before_it_starts_is_refused(self) -> None:
        with pytest.raises(IntegrityError, match="window_ordered"):
            _corridor(valid_from=JULY, valid_to=JANUARY)


class TestATariffKnowsWhichRungItAnswersOn:
    def test_a_region_row_may_not_carry_a_country(self) -> None:
        """Steps 3 and 4 are the same table. A row claiming both would answer
        on whichever the ladder tried first, which is not a decision anybody
        made."""
        with pytest.raises(IntegrityError, match="scope_columns_coherent"):
            _tariff(scope=TariffScope.REGION, region_id=7, country_id=1)

    def test_a_country_row_may_not_carry_a_region(self) -> None:
        with pytest.raises(IntegrityError, match="scope_columns_coherent"):
            _tariff(scope=TariffScope.COUNTRY, country_id=1, region_id=7)

    def test_a_region_row_needs_a_region(self) -> None:
        with pytest.raises(IntegrityError, match="scope_columns_coherent"):
            _tariff(scope=TariffScope.REGION, region_id=None)

    def test_a_country_row_is_accepted(self) -> None:
        row = _tariff(scope=TariffScope.COUNTRY, region_id=None, country_id=1)
        assert row.scope == TariffScope.COUNTRY


class TestATariffIsCoherent:
    def test_a_night_surcharge_without_a_window_is_refused(self) -> None:
        """A surcharge with no window applies never or always depending on who
        reads it. Requiring the window makes the intent explicit rather than
        emergent."""
        with pytest.raises(IntegrityError, match="night_surcharge_needs_a_window"):
            _tariff(night_surcharge_pct=Decimal("25.00"))

    def test_half_a_night_window_is_refused(self) -> None:
        with pytest.raises(IntegrityError, match="night_window_coherent"):
            _tariff(night_from=dt.time(22, 0), night_to=None)

    def test_a_whole_night_window_is_accepted(self) -> None:
        row = _tariff(
            night_surcharge_pct=Decimal("25.00"),
            night_from=dt.time(22, 0),
            night_to=dt.time(6, 0),
        )
        assert row.night_surcharge_pct == Decimal("25.00")

    def test_a_negative_rate_is_refused(self) -> None:
        with pytest.raises(IntegrityError, match="rates_non_negative"):
            _tariff(per_km_rate=Decimal("-0.4000"))

    def test_two_live_region_tariffs_for_one_class_are_refused(self) -> None:
        klass = _class()
        _tariff(vehicle_class=klass)
        with pytest.raises(IntegrityError):
            _tariff(vehicle_class=klass, base_fare=Decimal("20.00"))

    def test_two_live_country_tariffs_for_one_class_are_refused(self) -> None:
        """A separate constraint from the region one, because NULL is not equal
        to NULL: a single index over both id columns would never fire here."""
        klass = _class()
        _tariff(scope=TariffScope.COUNTRY, region_id=None, country_id=1, vehicle_class=klass)
        with pytest.raises(IntegrityError):
            _tariff(scope=TariffScope.COUNTRY, region_id=None, country_id=1, vehicle_class=klass)

    def test_a_region_tariff_and_a_country_tariff_coexist(self) -> None:
        """Steps 3 and 4 are both meant to be configured at once — that is what
        makes step 4 a fallback rather than a replacement."""
        klass = _class()
        _tariff(vehicle_class=klass)
        national = _tariff(
            scope=TariffScope.COUNTRY, region_id=None, country_id=1, vehicle_class=klass
        )
        assert national.pk is not None


class TestAVehicleClassIsReferenceData:
    def test_a_class_still_priced_cannot_be_deleted(self) -> None:
        """`PROTECT`. Removing STANDARD out from under a corridor would leave a
        price nobody can buy and a row nobody can read."""
        klass = _class()
        _corridor(vehicle_class=klass)
        with (
            pytest.raises(Exception, match="(?i)protected|foreign key"),
            transaction.atomic(),
        ):
            klass.hard_delete()

    def test_two_live_classes_may_not_share_a_code(self) -> None:
        _class("VAN")
        with pytest.raises(IntegrityError, match="code_unique_alive"):
            _class("VAN")

    def test_a_class_with_no_seats_is_refused(self) -> None:
        with pytest.raises(IntegrityError, match="seats_positive"):
            _class("EMPTY", seats=0)


class TestTheWaitingRateIsStoredAndUnread:
    """§12.4 lists `waiting_rate_per_minute` as "applied after free waiting
    allowance", and the SRS defines that allowance nowhere — no Appendix B key,
    no other mention. `wait.airport_minutes` and `wait.standard_minutes` are
    dispatch no-show thresholds, not billing allowances, and reading them here
    would invent a business rule the working agreement forbids inventing.

    So the column exists, so that §38's SHOULD-HAVE vehicle hire is not
    precluded by the schema, and nothing reads it. This test is what makes its
    arrival a decision rather than a discovery.
    """

    def test_the_column_exists_and_defaults_to_zero(self) -> None:
        assert _tariff().waiting_rate_per_minute == Decimal("0.0000")

    def test_no_pricing_code_reads_it(self) -> None:
        root = Path(__file__).resolve().parents[3]
        readers = [
            path
            for path in root.rglob("apps/**/*.py")
            if "waiting_rate_per_minute" in path.read_text(encoding="utf-8")
            and path.name not in {"models.py", "test_tariff_model.py"}
            and "migrations" not in path.parts
        ]
        assert readers == [], (
            "waiting_rate_per_minute is read by "
            f"{[str(p.relative_to(root)) for p in readers]}. §12.4 applies it "
            "'after free waiting allowance' and the SRS defines no such "
            "allowance, so charging for waiting means deciding what the "
            "allowance is — and recording that decision — first."
        )
