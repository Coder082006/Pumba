"""Put identity's tables under the §7.2 trigger, and seed the §5.2 roles."""

from __future__ import annotations

from django.db import migrations

from apps.common.db import attach_updated_at_trigger

#: SRS §5.2, in the order the table lists them. `code` is the shared
#: vocabulary of `apps.common.authz.Role` — the two are asserted equal by
#: `test_roles_seed_matches_the_enum`, so a role added to one and not the
#: other fails the build rather than silently granting nothing.
ROLES = [
    ("TOURIST", "Tourist"),
    ("DRIVER", "Driver"),
    ("PROVIDER_OWNER", "Provider owner"),
    ("PROVIDER_STAFF", "Provider staff"),
    ("SUPPORT_AGENT", "Support agent"),
    ("FINANCE_OFFICER", "Finance officer"),
    ("CATALOGUE_ADMIN", "Catalogue administrator"),
    ("COMPLIANCE_ADMIN", "Compliance administrator"),
    ("SUPER_ADMIN", "Super administrator"),
]

TABLES = ["user", "role", "user_role", "tourist_profile", "session", "device", "one_time_token"]


def seed_roles(apps, schema_editor):
    Role = apps.get_model("identity", "Role")
    for code, name in ROLES:
        Role.objects.update_or_create(code=code, defaults={"name": name})


def unseed_roles(apps, schema_editor):
    Role = apps.get_model("identity", "Role")
    Role.objects.filter(code__in=[c for c, _ in ROLES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0001_initial"),
        ("common", "0002_updated_at_trigger"),
    ]

    operations = [
        *[attach_updated_at_trigger(table) for table in TABLES],
        migrations.RunPython(seed_roles, unseed_roles),
    ]
