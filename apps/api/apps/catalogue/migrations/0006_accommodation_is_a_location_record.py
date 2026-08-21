"""room_type leaves, and accommodation is reduced to a location record.

ADR 0013, SRS v1.2 §7.5.7 and §14. Accommodation is no longer a bookable
product: an `accommodation` row now holds where a property is and when its day
starts and ends, and a STAY itinerary item anchors to it with no provider, no
price, no booking and no inventory.

The columns removed here are exactly the ones that made claims only the
property's owner could make — its provider, its star rating, its amenities, its
child policy, its cancellation policy and its booking cutoff — plus the
denormalised rating pair, which had nothing to aggregate once accommodation
stopped being bookable. That removal is what makes Appendix C's forty seeded
Zanzibar properties honest reference data rather than unattributed supply.

Additive, like `inventory/0002` and for the same reason: `0003` and `0005` are
pushed and quoted by ADR 0011 and ADR 0012, and rewriting them would falsify
two accepted records.

**The dependency on `inventory/0002` is load-bearing and is not an import.**
`room_availability` carries `room_availability_room_type_fk`, so it has to go
first. ADR 0012's rule holds: a migration dependency is a string.
"""

from django.db import migrations

from apps.common.db import attach_updated_at_trigger


class Migration(migrations.Migration):
    dependencies = [
        ("catalogue", "0005_search_vectors"),
        # Not decorative. room_availability references room_type by FOREIGN KEY.
        ("inventory", "0002_room_availability_deferred_to_v2"),
    ]

    operations = [
        # First forward is last backward: `DeleteModel` reversed rebuilds the
        # table from migration state, and state carries no triggers, so the
        # §7.2 re-attach has to be written before the delete to run after it.
        migrations.RunSQL(
            sql=migrations.RunSQL.noop,
            reverse_sql=attach_updated_at_trigger("room_type").sql,
        ),
        migrations.DeleteModel(name="RoomType"),
        # --- accommodation is reduced --------------------------------------
        migrations.RemoveIndex(model_name="accommodation", name="accommodation_amenities_gin"),
        migrations.RemoveConstraint(
            model_name="accommodation", name="accommodation_star_rating_in_range"
        ),
        migrations.RemoveConstraint(
            model_name="accommodation", name="accommodation_booking_cutoff_non_negative"
        ),
        migrations.RemoveConstraint(
            model_name="accommodation", name="accommodation_rating_avg_in_range"
        ),
        migrations.RemoveConstraint(
            model_name="accommodation", name="accommodation_rating_count_non_negative"
        ),
        migrations.RemoveField(model_name="accommodation", name="provider_id"),
        migrations.RemoveField(model_name="accommodation", name="star_rating"),
        migrations.RemoveField(model_name="accommodation", name="amenities"),
        migrations.RemoveField(model_name="accommodation", name="cancellation_policy"),
        migrations.RemoveField(model_name="accommodation", name="child_policy"),
        migrations.RemoveField(model_name="accommodation", name="booking_cutoff_hours"),
        migrations.RemoveField(model_name="accommodation", name="rating_avg"),
        migrations.RemoveField(model_name="accommodation", name="rating_count"),
    ]
