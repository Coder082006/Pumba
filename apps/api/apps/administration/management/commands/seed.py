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
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalogue import services as catalogue

__all__ = ["Command"]

#: Repo-relative, resolved from this file so the command works from any
#: working directory — `make seed`, a container shell and CI all differ.
DEFAULT_ROOT = Path(__file__).resolve().parents[5] / "database" / "seeds"


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
        root: Path = options["root"] / "catalogue"
        if not root.is_dir():
            raise CommandError(f"no seed directory at {root}")

        # One transaction across every file. A half-loaded catalogue — regions
        # without their destinations — is worse than an unloaded one, because
        # it looks loaded.
        try:
            with transaction.atomic():
                results = [self._load(root, stem, key) for stem, key in catalogue.SEED_FILES]
                if options["dry_run"]:
                    self.stdout.write(self.style.WARNING("dry run — rolling back"))
                    transaction.set_rollback(True)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        for result in results:
            self.stdout.write(self.style.SUCCESS(str(result)))

    def _load(self, root: Path, stem: str, entity_key: str) -> catalogue.SeedResult:
        path = root / f"{stem}.json"
        if not path.is_file():
            raise CommandError(f"missing seed file {path}")
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise CommandError(f"{path} must hold a JSON array")
        return catalogue.load_seed(entity_key, rows)
