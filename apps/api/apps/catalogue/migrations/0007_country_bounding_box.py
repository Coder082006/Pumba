"""Every country states the box its coordinates must fall inside.

The error this guards against is a latitude and longitude entered the wrong way
round. `Coordinates` already refuses anything outside ±90 / ±180, but a
transposed Zanzibar pair is individually in range — latitude 39.19 is a real
latitude in Turkey, longitude -6.16 is a real longitude off West Africa — so
the row writes, the audit entry records it, the API serves it, and the hotel
appears six thousand kilometres out to sea. Nothing downstream can tell.

The bound lives on `country` rather than in code because §4.2 forbids this
module knowing where the market is. Opening Kenya is then four more numbers in
the same console form, not a deployment.

**The placeholder.** These columns are NOT NULL, because a nullable bound with
a "skip the check when it is absent" rule is an exemption that silently
disables the guard for every row beneath that country. Adding a NOT NULL column
to an existing table needs a value for the rows already there, and there is no
correct one — so the whole-world box is used, `preserve_default=False` keeps it
off the model, and `make seed` overwrites it with the real box on the next run.

A country left holding the whole world would pass every bounds check
vacuously, which is the one failure this migration could introduce. It is not
left to vigilance: `test_the_shipped_data_would_notice_a_swap` transposes every
coordinate in the seed set and requires its country to reject it, and the world
box fails that test for every row at once.
"""

from django.db import migrations, models


def _degrees(**kwargs):
    return models.DecimalField(max_digits=10, decimal_places=7, **kwargs)


#: Not a sensible bound — a deliberately absurd one, so that a country still
#: holding it is obvious in a query and provably caught by the swap test.
_WHOLE_WORLD = {
    "min_latitude": "-90.0000000",
    "min_longitude": "-180.0000000",
    "max_latitude": "90.0000000",
    "max_longitude": "180.0000000",
}


class Migration(migrations.Migration):
    dependencies = [("catalogue", "0006_accommodation_is_a_location_record")]

    operations = [
        *(
            migrations.AddField(
                model_name="country",
                name=name,
                field=_degrees(default=default),
                preserve_default=False,
            )
            for name, default in _WHOLE_WORLD.items()
        ),
        migrations.AddConstraint(
            model_name="country",
            constraint=models.CheckConstraint(
                condition=models.Q(min_latitude__lt=models.F("max_latitude")),
                name="country_bounds_latitude_ordered",
            ),
        ),
        # Longitude is deliberately NOT ordered: min > max is how a box that
        # crosses the antimeridian is written, which is a real geography
        # (Fiji, Kiribati) and not a mistake. Only a zero-width box is refused.
        migrations.AddConstraint(
            model_name="country",
            constraint=models.CheckConstraint(
                condition=~models.Q(min_longitude=models.F("max_longitude")),
                name="country_bounds_longitude_not_degenerate",
            ),
        ),
    ]
