"""§12.2's leg definition reaches the table — ADR 0023 decision 5.

    "A transfer leg is defined by an origin, a target, a departure instant, a
    party size, a luggage count and a vehicle class ... and the binding is
    stored on the itinerary item so that the leg can be re-priced identically
    later."

§7.5.11's column list has neither `vehicle_class` nor `luggage_count`, so both
arrive here under ADR 0007's rule for tables the SRS names but does not fully
specify — the same precedent ADR 0019 used for `estimate_quality`, on this same
table.

**The backfill is not cosmetic.** The CHECK below makes both columns NOT NULL
on a transfer, and every transfer written before Phase 6 has neither. Without
the `RunPython` in the middle, this migration applies cleanly against an empty
database and fails against every database anybody has planned a trip in —
which is the shape of failure that reaches production and not CI. The rows are
given the smallest class and no luggage, which is what an unpriced leg carries
anyway; §24.17 lets the tourist change it and re-quote.
"""

from django.db import migrations, models

#: What a leg written before Phase 6 is given. The smallest §12.4 class and no
#: luggage — the same pair `_write_inserted_transfer` uses for a leg it cannot
#: price, because that is exactly what these rows are.
_BACKFILL_CLASS = "STANDARD"


def _name_a_class_on_every_existing_transfer(apps, schema_editor) -> None:
    item = apps.get_model("trip", "ItineraryItem")
    archive = apps.get_model("trip", "ItineraryItemArchive")
    for model in (item, archive):
        model.objects.filter(item_type="TRANSFER", vehicle_class__isnull=True).update(
            vehicle_class=_BACKFILL_CLASS, luggage_count=0
        )


class Migration(migrations.Migration):
    dependencies = [
        ("trip", "0003_item_position_unique_is_deferred"),
    ]

    operations = [
        migrations.AddField(
            model_name="itineraryitem",
            name="luggage_count",
            field=models.PositiveSmallIntegerField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="itineraryitem",
            name="vehicle_class",
            field=models.CharField(blank=True, default=None, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="itineraryitemarchive",
            name="luggage_count",
            field=models.PositiveSmallIntegerField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="itineraryitemarchive",
            name="vehicle_class",
            field=models.CharField(blank=True, default=None, max_length=20, null=True),
        ),
        # Before the constraint, and in the same migration: see the docstring.
        migrations.RunPython(
            _name_a_class_on_every_existing_transfer,
            migrations.RunPython.noop,
            elidable=False,
        ),
        migrations.AddConstraint(
            model_name="itineraryitem",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("item_type", "TRANSFER"), _negated=True),
                    models.Q(("luggage_count__isnull", False), ("vehicle_class__isnull", False)),
                    _connector="OR",
                ),
                name="itinerary_item_transfer_names_a_vehicle_class",
            ),
        ),
        migrations.AddConstraint(
            model_name="itineraryitem",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("item_type", "TRANSFER"),
                    models.Q(("luggage_count__isnull", True), ("vehicle_class__isnull", True)),
                    _connector="OR",
                ),
                name="itinerary_item_only_transfers_name_a_vehicle_class",
            ),
        ),
    ]
