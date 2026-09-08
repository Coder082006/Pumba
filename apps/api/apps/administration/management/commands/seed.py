"""`manage.py seed` — the Appendix C seed set.

    Appendix C: "The seed set delivered in database/seeds/ and loadable
    through the admin console."

**Why the command lives in `administration`.** §6.4 gives this module
dependencies of "all", which makes it the one place a cross-module operation
can be assembled. Seeding is exactly that: Phase 3 writes catalogue rows, and
later phases add transfer corridors, tariffs, commission rules and
notification templates, each owned by a different module. A loader in
`catalogue` would have to be joined by five more, or grow imports it is not
allowed to have.

What this command knows is *where the files are and what order they load in*.
What each row means is the owning module's business, reached through its
`services.py` and nothing else (§6.5 rule 1) — which is also why the loaded
rows are audited exactly like a console write.

**The data is JSON, not Python, and that is a constraint rather than a
preference.** §4.2 forbids a destination name appearing as a string literal in
application code, and `tests/test_destination_independence.py` walks the AST of
every module to enforce it. A `seeds.py` holding forty Zanzibar hotels would
fail that test, and rightly: the moment seed data is code, the temptation to
branch on it is one edit away.

**Idempotent.** Re-running is a no-op, because a seed row identifies itself by
ISO code or slug and an existing live row is updated rather than duplicated.
That matters more than it sounds: this runs on every fresh checkout, in CI, and
again whenever somebody corrects a coordinate.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalogue import services as catalogue
from apps.transport import services as transport

__all__ = ["Command", "find_seed_root", "DEFAULT_ROOT"]


#: Every ancestor of this file, each asked whether it holds `database/seeds`.
#:
#: Two layouts are real and neither is wrong. In a checkout this file is
#: `<repo>/apps/api/apps/administration/management/commands/seed.py` and the
#: seeds are `<repo>/database/seeds`. In the container image it is
#: `/app/apps/administration/...` and the repository's `database/` is
#: bind-mounted at `/database` — so the number of hops differs by one, and the
#: container has fewer ancestors than the checkout has.
#:
#: A fixed `parents[N]` satisfies exactly one of them. `parents[5]` satisfied
#: the container and silently resolved to `<repo>/apps/database/seeds` on a
#: host, so `make seed` and the seed tests failed everywhere CI runs while
#: every container run stayed green. Searching is not cleverness here; it is
#: the only thing that is true in both layouts.
def find_seed_root(start: Path) -> Path:
    """The nearest ancestor of `start` holding `database/seeds`.

    A function rather than an inline expression so it can be tested against a
    synthetic tree. "It resolves correctly on this machine" is precisely the
    evidence that hid the original defect — the container answered right and
    nothing else did.

    Falls back to the filesystem-root candidate rather than raising: Django
    imports every management command at startup, so a deployment without the
    seed directory mounted must not fail to boot. `handle` reports the missing
    directory by name instead.
    """
    candidates = tuple(parent / "database" / "seeds" for parent in start.resolve().parents)
    return next((c for c in candidates if c.is_dir()), candidates[-1])


DEFAULT_ROOT = find_seed_root(Path(__file__))


class Command(BaseCommand):
    help = "Load the Appendix C seed set. Idempotent; safe to re-run."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--root",
            type=Path,
            default=DEFAULT_ROOT,
            help="Directory holding the seed files (default: database/seeds).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read and validate every file, then roll back without writing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        seeds: Path = options["root"]
        root: Path = seeds / "catalogue"
        if not root.is_dir():
            raise CommandError(f"no seed directory at {root}")

        # One transaction across every file. A half-loaded catalogue — regions
        # without their destinations — is worse than an unloaded one, because
        # it looks loaded.
        try:
            with transaction.atomic():
                # Typed as the two loaders' union rather than one of them:
                # both report the same three fields and neither may import the
                # other's class, which is the §6.4 boundary showing through.
                results: list[catalogue.SeedResult | transport.SeedResult] = [
                    self._load(root, stem, key) for stem, key in catalogue.SEED_FILES
                ]
                # Media last and through its own loader: it is not a
                # `CatalogueEntity` (see `load_media_seed` for why), and every
                # row names an owner that the files above have to have created.
                # Schedules before media and after everything in SEED_FILES:
                # each names an activity the file above it created, and it is
                # not a `CatalogueEntity` (see `load_schedule_seed` for why).
                results.append(self._load_schedules(root))
                results.append(self._load_media(root))
                # Transport last: a corridor names two destinations and a
                # tariff names a country, so every row it resolves has to have
                # been written by the files above it.
                results.extend(self._load_transport(seeds))
                if options["dry_run"]:
                    self.stdout.write(self.style.WARNING("dry run — rolling back"))
                    transaction.set_rollback(True)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        for result in results:
            self.stdout.write(self.style.SUCCESS(str(result)))

    def _load_schedules(self, root: Path) -> catalogue.SeedResult:
        path = root / f"{catalogue.SCHEDULE_SEED_FILE}.json"
        if not path.is_file():
            # Optional in the same way media is, and for a narrower reason: a
            # checkout without it has activities that cannot be booked on any
            # date, which is a legitimate state for a catalogue whose supply
            # is coming from the Phase 11 portal.
            return catalogue.SeedResult("activity_schedule", 0, 0)
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise CommandError(f"{path} must hold a JSON array")
        return catalogue.load_schedule_seed(rows)

    def _load_media(self, root: Path) -> catalogue.SeedResult:
        path = root / f"{catalogue.MEDIA_SEED_FILE}.json"
        if not path.is_file():
            # Optional, unlike the entity files. Photography is content rather
            # than reference data, and a checkout without it should still
            # produce a working catalogue — with reserved, empty image boxes.
            return catalogue.SeedResult("media", 0, 0)
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise CommandError(f"{path} must hold a JSON array")
        return catalogue.load_media_seed(rows)

    # -- transport ----------------------------------------------------------
    #
    # Appendix C's corridors and tariffs. This is the second module the loader
    # writes, and the first time its "later phases add transfer corridors,
    # tariffs" note comes true.
    #
    # **The catalogue references are resolved here.** A corridor row names
    # `"nungwi"` and a tariff row names `"TZ"`, because a seed file is written
    # by a person; the tables store ids (ADR 0012), and §6.4 gives `transport`
    # no way to turn one into the other. `administration` has "all (read via
    # interfaces)", so it does the resolving and hands `transport` integers —
    # the same division ADR 0023 makes for the quote endpoint.

    def _load_transport(self, root: Path) -> list[transport.SeedResult]:
        directory = root / "transport"
        if not directory.is_dir():
            # Optional in the way media is. A checkout without it has a
            # catalogue that plans but cannot price a transfer, which is a
            # legitimate state and exactly what Phase 5 shipped.
            return []

        classes = transport.load_vehicle_class_seed(
            self._rows(directory / "01-vehicle-classes.json")
        )
        tariffs = transport.load_tariff_seed(
            [self._scoped(row) for row in self._rows(directory / "02-transfer-tariffs.json")]
        )
        corridors = transport.load_corridor_seed(
            [self._routed(row) for row in self._rows(directory / "03-transfer-corridors.json")]
        )
        return [classes, tariffs, corridors]

    def _rows(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            raise CommandError(f"missing seed file {path}")
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise CommandError(f"{path} must hold a JSON array")
        return rows

    def _scoped(self, row: dict[str, Any]) -> dict[str, Any]:
        """A tariff row's `region` or `country` key, resolved to an id."""
        resolved = dict(row)
        for kind in ("region", "country"):
            key = resolved.pop(kind, None)
            if key is None:
                continue
            found = catalogue.resolve_scope_ref(kind, key)
            if found is None:
                raise CommandError(
                    f"tariff names {kind} {key!r}, which the catalogue has no row for"
                )
            resolved[f"{kind}_id"] = found
        return resolved

    def _routed(self, row: dict[str, Any]) -> dict[str, Any]:
        """A corridor row's two destination slugs, resolved to ids."""
        resolved = dict(row)
        for end in ("origin", "target"):
            slug = resolved.pop(f"{end}_destination")
            ref = catalogue.resolve_planning_ref(slug, today=date.today())  # noqa: DTZ011
            if ref is None:
                raise CommandError(
                    f"corridor names destination {slug!r}, which the catalogue has no "
                    "live row for. Corridors load after 04-destinations.json."
                )
            resolved[f"{end}_destination_id"] = ref.storage_id
        return resolved

    def _load(self, root: Path, stem: str, entity_key: str) -> catalogue.SeedResult:
        path = root / f"{stem}.json"
        if not path.is_file():
            raise CommandError(f"missing seed file {path}")
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise CommandError(f"{path} must hold a JSON array")
        return catalogue.load_seed(entity_key, rows)
