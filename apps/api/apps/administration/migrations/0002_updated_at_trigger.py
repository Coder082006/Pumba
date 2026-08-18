"""Put audit_log under the §7.2 `updated_at` trigger."""

from __future__ import annotations

from django.db import migrations

from apps.common.db import attach_updated_at_trigger


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0001_initial"),
        ("common", "0002_updated_at_trigger"),
    ]

    operations = [attach_updated_at_trigger("audit_log")]
