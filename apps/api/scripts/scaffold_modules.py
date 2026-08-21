"""One-shot generator for the module skeletons of SRS §6.4.

Run once during Phase 1. Kept in the repository so the layout of a module is
declared in one place rather than copied by hand, and so adding a module later
cannot drift from the layer structure of SRS §8.2.

    uv run python scripts/scaffold_modules.py

Never overwrites a file that already has content.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

APPS_DIR = Path(__file__).resolve().parent.parent / "apps"


@dataclass(frozen=True)
class Module:
    name: str
    layer: str
    owns: str
    interface: str
    depends: str
    note: str = ""


# SRS §6.4 Module Catalogue, verbatim. `layer` is the topological rank derived
# from the dependency column — see docs/IMPLEMENTATION-PLAN.md §3.
MODULES = [
    Module(
        "identity",
        "L0",
        "user, role, user_role, tourist_profile, session, device",
        "authenticate(), issue_tokens(), get_principal()",
        "—",
    ),
    Module(
        "location",
        "L0",
        "route_cache, driver_location, geofence",
        "route(), distance_matrix(), geocode(), last_known()",
        "— (external port)",
    ),
    Module(
        "notify",
        "L0",
        "notification, notification_template, notification_delivery",
        "emit(event, recipients, context)",
        "— (external ports)",
    ),
    Module(
        "catalogue",
        "L1",
        "country, region, destination, attraction, activity, activity_schedule, "
        "accommodation, cancellation_policy, tag, media",
        "search_activities(), get_destination(), list_accommodation()",
        "location",
    ),
    Module(
        "provider",
        "L1",
        "provider, provider_document, provider_staff, driver, vehicle",
        "is_verified(), eligible_drivers(), get_payout_account()",
        "identity",
    ),
    Module(
        "inventory",
        "L2",
        "activity_departure, inventory_hold",
        "check_availability(), hold(), commit(), release()",
        "catalogue",
    ),
    Module(
        "transport",
        "L2",
        "transfer_corridor, transfer_tariff, driver_assignment, driver_offer",
        "quote_transfer(), assign_driver(), dispatch_offer()",
        "location, provider",
    ),
    Module(
        "trip",
        "L3",
        "trip, itinerary, itinerary_item, trip_flight",
        "create_trip(), regenerate_itinerary(), validate_itinerary()",
        "catalogue, transport",
    ),
    Module(
        "booking",
        "L4",
        "booking, booking_accommodation, booking_activity, booking_transfer, "
        "booking_status_history",
        "create_basket(), confirm(), cancel(), transition()",
        "inventory, trip, provider",
    ),
    Module(
        "payment",
        "L5",
        "payment, payment_transaction, refund, payment_webhook_event",
        "initiate(), verify(), refund()",
        "booking, trip, inventory",
        note="SRS §6.4 says 'booking (via events)' only. But §9.4.7 reads trip.status\n"
        "and trip.total_amount synchronously, and §20.8's confirmation routine\n"
        "writes to booking and inventory inside the webhook transaction. The\n"
        "contract reflects §9.4/§20.8. Revisit at Phase 8 — issue S3 in\n"
        "docs/IMPLEMENTATION-PLAN.md.",
    ),
    Module(
        "messaging", "L5", "conversation, message, message_report", "post(), thread()", "booking"
    ),
    Module(
        "review",
        "L5",
        "review, review_response, review_flag, rating_aggregate",
        "submit(), moderate(), aggregate_for()",
        "booking",
    ),
    Module(
        "finance",
        "L6",
        "commission_rule, ledger_entry, provider_balance, payout, payout_item, fx_rate",
        "accrue(), settle(), build_payout_batch()",
        "payment, booking",
        note="Ledger is append-only: no UPDATE, no DELETE on financial rows.\n"
        "Corrections are new reversing entries (SRS §22.3, principle A2).",
    ),
    Module(
        "administration",
        "L7",
        "audit_log, system_setting, feature_flag, support_ticket",
        "record_audit(), get_setting()",
        "all (read via interfaces)",
        note="Owns the system_setting table and its audited write path. The *read*\n"
        "port is apps.common.config.get_setting — see issue S1 in\n"
        "docs/IMPLEMENTATION-PLAN.md for why it cannot live here.",
    ),
]

LAYER_FILES = {
    "models.py": "Data-access layer (SRS §8.2 layer 4).\n\n    Owns:\n{owns_block}",
    "repositories.py": "Data-access layer (SRS §8.2 layer 4). All ORM writes; returns DTOs.",
    "selectors.py": "Data-access layer (SRS §8.2 layer 4). Read queries; returns DTOs.",
    "services.py": (
        "Application layer (SRS §8.2 layer 2).\n\n"
        "    The ONLY module boundary. Other modules call this and nothing else\n"
        "    (SRS §6.5 rule 1). Orchestrates a use case in one transaction and\n"
        "    emits domain events.\n\n"
        "    Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).\n\n"
        "    Public interface: {interface}"
    ),
    "serializers.py": "Interface layer (SRS §8.2 layer 1). Syntactic validation only.",
    "views.py": "Interface layer (SRS §8.2 layer 1). No business logic, no ORM queries.",
    "permissions.py": "Interface layer (SRS §8.2 layer 1). Role checks, then ownership checks.",
    "urls.py": "Interface layer (SRS §8.2 layer 1).",
    "tasks.py": "Infrastructure layer (SRS §8.2 layer 5). Celery tasks.",
    "dto.py": (
        "Data transfer objects.\n\n"
        "    Importable across module boundaries alongside services (SRS §6.5\n"
        "    rule 1). Plain frozen dataclasses — no ORM, no Django."
    ),
}

PACKAGE_FILES = {
    "domain": (
        "Domain layer (SRS §8.2 layer 3).\n\n"
        "    Pure functions over value objects. NO ORM imports, NO I/O, no Django.\n"
        "    Enforced by import-linter contract 'domain-layer-is-pure'.\n\n"
        "    This is the audit-sensitive logic — pricing, policy evaluation,\n"
        "    validation rules and state-machine guards — and it carries the 95%\n"
        "    coverage gate (SRS §35.3, §36.2)."
    ),
    "adapters": (
        "Infrastructure layer (SRS §8.2 layer 5).\n\n"
        "    The ONLY place a third-party SDK may be imported (SRS §36.2).\n"
        "    Enforced by import-linter contract 'vendor-sdks-only-in-adapters'."
    ),
    "tests": "Tests for the {name} module.",
    "migrations": "",
}


def _wrap(label: str, value: str, width: int = 96) -> list[str]:
    """Wrap a `label: value` pair, hanging-indenting continuations."""
    indent = " " * len(label)
    return textwrap.wrap(
        value,
        width=width,
        initial_indent=label,
        subsequent_indent=indent,
        break_long_words=False,
    )


def header(module: Module) -> str:
    lines = [
        f"{module.name} module — SRS §6.4.",
        "",
        *_wrap("Owns:       ", module.owns),
        *_wrap("Interface:  ", module.interface),
        *_wrap("Depends on: ", module.depends),
        f"Layer:      {module.layer}",
    ]
    if module.note:
        lines += ["", *module.note.split("\n")]
    return "\n".join(lines)


def write(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8").strip():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    created = 0
    for module in MODULES:
        root = APPS_DIR / module.name

        created += write(root / "__init__.py", f'"""{header(module)}\n"""\n')

        class_name = module.name.title().replace("_", "")
        created += write(
            root / "apps.py",
            "from django.apps import AppConfig\n\n\n"
            f"class {class_name}Config(AppConfig):\n"
            '    default_auto_field = "django.db.models.BigAutoField"\n'
            f'    name = "apps.{module.name}"\n'
            f'    label = "{module.name}"\n'
            f'    verbose_name = "{module.name.title()}"\n',
        )

        owns_block = "\n".join(
            textwrap.wrap(module.owns, width=92, initial_indent=" " * 8, subsequent_indent=" " * 8)
        )
        for filename, doc in LAYER_FILES.items():
            body = doc.format(
                owns=module.owns,
                owns_block=owns_block,
                interface=module.interface,
                name=module.name,
            )
            created += write(
                root / filename,
                f'"""{body}\n\nPhase 1: skeleton only.\n"""\n',
            )

        for pkg, doc in PACKAGE_FILES.items():
            body = doc.format(name=module.name)
            content = f'"""{body}\n"""\n' if body else ""
            created += write(root / pkg / "__init__.py", content)

    print(f"scaffold complete: {created} files created")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
