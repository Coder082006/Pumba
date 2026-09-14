"""`booking_activity.confirmation_mode` — ADR 0025, second addendum.

Frozen at the basket so §20.8 step 12 branches on what the tourist was told,
not on whatever the listing says by the time payment is captured.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0002_booking"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookingactivity",
            name="confirmation_mode",
            field=models.CharField(default="INSTANT", max_length=20),
        ),
        migrations.AddConstraint(
            model_name="bookingactivity",
            constraint=models.CheckConstraint(
                condition=models.Q(("confirmation_mode__in", ["INSTANT", "ON_REQUEST"])),
                name="booking_activity_confirmation_mode_known",
            ),
        ),
    ]
