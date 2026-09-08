"""The transport seed loaders — SRS Appendix C, §12.4.

`administration` resolves every catalogue reference before these are called
(ADR 0023), so what is tested here is what a row *means*, not where it came
from.

**The interesting property is idempotency across an edit**, not merely across a
re-run. A loader that only matched the direction a row was written in turned
144 corridors into 176 the first time the seed file was reordered so that
gateways read as origins — a presentation change that silently doubled a route.
Both directions then answered for the same journey and their prices could drift
apart with nothing to notice. `test_flipping_a_bidirectional_row_updates_it`
is that defect, kept.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from apps.transport import services
from apps.transport.models import TariffScope, TransferCorridor, TransferTariff, VehicleClass

pytestmark = pytest.mark.django_db

ZNZ, NUNGWI, PAJE = 1, 2, 3
NORTH, TANZANIA = 20, 100

CLASSES: list[dict[str, Any]] = [
    {"code": "STANDARD", "name": "Standard", "seats": 4, "luggage_capacity": 3, "display_order": 1},
    {"code": "VAN", "name": "Van", "seats": 7, "luggage_capacity": 6, "display_order": 2},
]


def _corridor_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "origin_destination_id": ZNZ,
        "target_destination_id": NUNGWI,
        "vehicle_class": "STANDARD",
        "fixed_price": "120000.00",
        "currency": "TZS",
        "is_bidirectional": True,
        "valid_from": dt.date(2026, 1, 1),
    }
    row.update(overrides)
    return row


def _tariff_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "scope": TariffScope.COUNTRY,
        "country_id": TANZANIA,
        "vehicle_class": "STANDARD",
        "base_fare": "30000.00",
        "per_km_rate": "1620.0000",
        "currency": "TZS",
        "valid_from": dt.date(2026, 1, 1),
    }
    row.update(overrides)
    return row


class TestVehicleClasses:
    def test_the_four_classes_load(self) -> None:
        result = services.load_vehicle_class_seed(CLASSES)
        assert (result.created, result.updated) == (2, 0)
        assert VehicleClass.objects.count() == 2

    def test_a_re_run_updates_rather_than_duplicating(self) -> None:
        services.load_vehicle_class_seed(CLASSES)
        result = services.load_vehicle_class_seed(CLASSES)
        assert (result.created, result.updated) == (0, 2)
        assert VehicleClass.objects.count() == 2

    def test_a_corrected_capacity_reaches_the_row(self) -> None:
        """The reason a re-run is a real operation rather than a no-op:
        somebody fixed a luggage figure and expects it to land."""
        services.load_vehicle_class_seed(CLASSES)
        services.load_vehicle_class_seed([{**CLASSES[0], "luggage_capacity": 4}])
        assert VehicleClass.objects.get(code="STANDARD").luggage_capacity == 4

    def test_a_row_without_a_code_is_refused(self) -> None:
        with pytest.raises(Exception, match="code"):
            services.load_vehicle_class_seed([{"name": "Nameless", "seats": 4}])


class TestTariffs:
    def test_they_load_and_re_load(self) -> None:
        services.load_vehicle_class_seed(CLASSES)
        assert services.load_tariff_seed([_tariff_row()]).created == 1
        assert services.load_tariff_seed([_tariff_row()]).updated == 1
        assert TransferTariff.objects.count() == 1

    def test_the_identity_is_scope_place_and_class(self) -> None:
        """The triple the ladder looks a tariff up by. A second row for the
        same one is a correction, not a new tariff — and the exclusion
        constraint agrees, so a loader that disagreed would fail on the second
        run rather than the first."""
        services.load_vehicle_class_seed(CLASSES)
        services.load_tariff_seed([_tariff_row(), _tariff_row(vehicle_class="VAN")])
        assert TransferTariff.objects.count() == 2

    def test_a_region_tariff_and_a_country_tariff_coexist(self) -> None:
        services.load_vehicle_class_seed(CLASSES)
        services.load_tariff_seed(
            [
                _tariff_row(),
                _tariff_row(scope=TariffScope.REGION, country_id=None, region_id=NORTH),
            ]
        )
        assert TransferTariff.objects.count() == 2

    def test_an_unknown_class_is_refused_by_name(self) -> None:
        """A tariff naming a class nobody seeded is a file-ordering mistake,
        and the message says which file fixes it."""
        with pytest.raises(Exception, match="01-vehicle-classes"):
            services.load_tariff_seed([_tariff_row(vehicle_class="HELICOPTER")])


class TestCorridors:
    def test_they_load_and_re_load(self) -> None:
        services.load_vehicle_class_seed(CLASSES)
        assert services.load_corridor_seed([_corridor_row()]).created == 1
        assert services.load_corridor_seed([_corridor_row()]).updated == 1
        assert TransferCorridor.objects.count() == 1

    def test_flipping_a_bidirectional_row_updates_it(self) -> None:
        """The defect this loader was written twice for.

        Reordering the seed file so a gateway reads as the origin is a
        presentation change. Matching on the written direction alone turned it
        into a second live corridor for the same journey — 144 rows became 176
        — and the two could then be priced differently with nothing to notice.
        """
        services.load_vehicle_class_seed(CLASSES)
        services.load_corridor_seed([_corridor_row()])

        flipped = _corridor_row(origin_destination_id=NUNGWI, target_destination_id=ZNZ)
        result = services.load_corridor_seed([flipped])

        assert (result.created, result.updated) == (0, 1)
        assert TransferCorridor.objects.count() == 1
        row = TransferCorridor.objects.get()
        assert (row.origin_destination_id, row.target_destination_id) == (NUNGWI, ZNZ)

    def test_a_one_way_corridor_keeps_its_direction_in_its_identity(self) -> None:
        """For a one-way route the direction *is* the route, so the reverse is
        a different corridor and must be able to carry a different price."""
        services.load_vehicle_class_seed(CLASSES)
        services.load_corridor_seed([_corridor_row(is_bidirectional=False)])
        services.load_corridor_seed(
            [
                _corridor_row(
                    origin_destination_id=NUNGWI,
                    target_destination_id=ZNZ,
                    is_bidirectional=False,
                    fixed_price="95000.00",
                )
            ]
        )
        assert TransferCorridor.objects.count() == 2

    def test_a_corrected_price_reaches_the_row(self) -> None:
        services.load_vehicle_class_seed(CLASSES)
        services.load_corridor_seed([_corridor_row()])
        services.load_corridor_seed([_corridor_row(fixed_price="125000.00")])
        assert TransferCorridor.objects.get().fixed_price == Decimal("125000.00")

    def test_a_second_class_on_the_same_route_is_a_second_corridor(self) -> None:
        services.load_vehicle_class_seed(CLASSES)
        services.load_corridor_seed([_corridor_row(), _corridor_row(vehicle_class="VAN")])
        assert TransferCorridor.objects.count() == 2

    def test_a_different_route_is_a_different_corridor(self) -> None:
        """The control. Without it every assertion above would also pass on a
        loader that had collapsed the whole file into one row."""
        services.load_vehicle_class_seed(CLASSES)
        services.load_corridor_seed([_corridor_row(), _corridor_row(target_destination_id=PAJE)])
        assert TransferCorridor.objects.count() == 2
