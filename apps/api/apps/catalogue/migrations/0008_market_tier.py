"""The market tier — SRS §4.2 as amended to v1.5. ADR 0018.

`market` goes between `country` and `region`, and `region.market` becomes
NOT NULL. One file rather than three, because the intermediate states are not
states anything should run in: a `market` table with no rows in it, or a
`region.market` that is nullable, are each a working database with the guard
switched off. Django runs a migration's operations in one transaction, so this
either arrives whole or does not arrive.

**The backfill.** It names Zanzibar and Pemba, which looks like the §4.2
prohibition on destination-specific logic and is not: a data migration is a
one-time correction to rows that already exist, not a branch the application
evaluates. There is no generic rule available here — nothing in the schema
knows that three of the five seeded regions are one island and two are
another, which is precisely the knowledge the new tier exists to hold. Written
against the slugs actually present, and inert on an empty database, where
`manage.py seed` creates the markets instead.

**The composite key.** `region` keeps `country_id` alongside `market_id`
because it is a join four `country_path` chains and both `select_related`
trees already walk. Denormalised columns disagree unless something stops them,
so `market` gets a UNIQUE on `(id, country_id)` and `region` a composite
FOREIGN KEY on `(market_id, country_id)` referencing it. PostgreSQL then
refuses a region whose market sits in a different country. Django models
neither, so both are raw SQL — the treatment ADR 0012's cross-module keys get
in `inventory/0001`.
"""

from __future__ import annotations

import uuid
from typing import Any

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

#: `region(market_id, country_id)` must name a real `market(id, country_id)`.
#: The UNIQUE is not redundant with the primary key: a composite FOREIGN KEY
#: needs a UNIQUE over exactly the columns it references.
COMPOSITE_KEY = """
ALTER TABLE market
    ADD CONSTRAINT market_id_country_unique UNIQUE (id, country_id);

ALTER TABLE region
    ADD CONSTRAINT region_market_shares_country_fk
    FOREIGN KEY (market_id, country_id) REFERENCES market (id, country_id);
"""

DROP_COMPOSITE_KEY = """
ALTER TABLE region DROP CONSTRAINT region_market_shares_country_fk;
ALTER TABLE market DROP CONSTRAINT market_id_country_unique;
"""

#: Which seeded regions belong to which market, and what that market is.
#: `is_active` mirrors §4.1: Unguja is served, Pemba is "record created but
#: is_active = false". `launch_date` is null for both — Zanzibar is already
#: open, and Pemba is deferred rather than scheduled.
MARKETS: tuple[tuple[str, str, bool, tuple[str, ...]], ...] = (
    (
        "zanzibar",
        "Zanzibar",
        True,
        ("zanzibar-urban-west", "zanzibar-north", "zanzibar-central-south"),
    ),
    ("pemba", "Pemba", False, ("pemba-north", "pemba-south")),
)


def backfill(apps: Any, schema_editor: Any) -> None:
    """Give every existing region a market.

    Any region not named above — one an administrator created by hand before
    this ran — gets a market of its own named after it, rather than being
    swept into Zanzibar. Guessing wrong here would put a destination under a
    market that could hide it, and a market per orphan is visible and
    correctable; a wrong parent is neither.
    """
    Market = apps.get_model("catalogue", "Market")
    Region = apps.get_model("catalogue", "Region")

    for slug, name, is_active, region_slugs in MARKETS:
        regions = Region.objects.filter(slug__in=region_slugs, market__isnull=True)
        if not regions.exists():
            continue
        # `country` comes from the regions themselves. The seed has one
        # country, but reading it rather than assuming "TZ" keeps this correct
        # on a database that has more.
        #
        # `order_by()` before `distinct()` is load-bearing. `Region.Meta`
        # orders by `country__iso_code, name`, Django adds ORDER BY columns to
        # the SELECT, and DISTINCT then applies across all three — returning
        # one row per region rather than one per country, and creating the
        # same market once for each.
        countries = regions.order_by().values_list("country_id", flat=True).distinct()
        for country_id in countries:
            market = Market.objects.create(
                country_id=country_id, slug=slug, name=name, is_active=is_active
            )
            regions.filter(country_id=country_id).update(market=market)

    for region in Region.objects.filter(market__isnull=True):
        market = Market.objects.create(
            country_id=region.country_id,
            slug=region.slug,
            name=region.name,
            is_active=region.is_active,
        )
        Region.objects.filter(pk=region.pk).update(market=market)


def unfill(apps: Any, schema_editor: Any) -> None:
    """Reverse: detach every region, then drop the markets."""
    Market = apps.get_model("catalogue", "Market")
    Region = apps.get_model("catalogue", "Region")
    Region.objects.update(market=None)
    Market.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("catalogue", "0007_country_bounding_box")]

    operations = [
        migrations.CreateModel(
            name="Market",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now, editable=False
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(default=django.utils.timezone.now, editable=False),
                ),
                (
                    "public_id",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, default=None, editable=False, null=True),
                ),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(max_length=140)),
                ("summary", models.TextField(blank=True, default=None, null=True)),
                ("is_active", models.BooleanField(default=False)),
                ("launch_date", models.DateField(blank=True, default=None, null=True)),
                (
                    "country",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="markets",
                        to="catalogue.country",
                    ),
                ),
            ],
            options={"db_table": "market", "ordering": ["country__iso_code", "name"]},
        ),
        migrations.AddIndex(
            model_name="market",
            index=models.Index(fields=["country", "is_active"], name="market_country_active_idx"),
        ),
        migrations.AddConstraint(
            model_name="market",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("country", "slug"),
                name="market_slug_unique_alive_per_country",
            ),
        ),
        # Nullable only for the length of this migration. The column has to
        # exist before there is anything to point it at.
        migrations.AddField(
            model_name="region",
            name="market",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="regions",
                to="catalogue.market",
            ),
        ),
        migrations.RunPython(backfill, unfill),
        migrations.AlterField(
            model_name="region",
            name="market",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="regions",
                to="catalogue.market",
            ),
        ),
        migrations.RunSQL(sql=COMPOSITE_KEY, reverse_sql=DROP_COMPOSITE_KEY),
    ]
