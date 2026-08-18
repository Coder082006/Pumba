"""Architecture tests — SRS §6.5, §37.1.

Two things are verified here that no other test covers.

**The contracts actually bite.** SRS §37.1's acceptance criterion is "a
forbidden cross-module import fails CI". A contract suite that passes over a
codebase with no cross-module imports yet proves nothing — it would pass just
as happily if the contracts were misconfigured and matched nothing at all.
So `test_forbidden_cross_module_import_is_rejected` writes a genuine
violation, runs the linter, and asserts it is caught.

**Services return DTOs, not ORM instances.** SRS §6.5 rule 5 requires this and
import-linter cannot express it — it is a property of function signatures, not
of the import graph.
"""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parent.parent

MODULES = [
    "identity",
    "location",
    "notify",
    "catalogue",
    "provider",
    "inventory",
    "transport",
    "trip",
    "booking",
    "payment",
    "messaging",
    "review",
    "finance",
    "administration",
]


def _linter_command() -> list[str]:
    """Locate the `lint-imports` console script inside the active venv.

    `python -m importlinter` is not available — the package ships no
    `__main__` — so the console script is the only entrypoint.
    """
    bin_dir = Path(sys.executable).parent
    for candidate in ("lint-imports.exe", "lint-imports"):
        path = bin_dir / candidate
        if path.exists():
            return [str(path)]
    raise RuntimeError(f"lint-imports not found in {bin_dir}")


def _run_import_linter() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _linter_command(),
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_contracts_pass_on_a_clean_tree() -> None:
    result = _run_import_linter()
    assert (
        result.returncode == 0
    ), f"import-linter failed on an unmodified tree:\n{result.stdout}\n{result.stderr}"
    assert "Contracts: 31 kept, 0 broken." in result.stdout


def test_forbidden_cross_module_import_is_rejected() -> None:
    """SRS §37.1: "a forbidden cross-module import fails CI".

    `inventory` may depend on `catalogue` only (SRS §6.4). `location` is
    reachable transitively through catalogue but must not be imported
    directly, so this is the exact violation the non-transitive reading of
    the dependency table is meant to catch.
    """
    probe = API_ROOT / "apps" / "inventory" / "_arch_probe.py"
    probe.write_text(
        "# Temporary file written by tests/test_architecture.py.\n"
        "# If this file is present in a commit, the test failed to clean up.\n"
        "from apps.location import services  # noqa: F401\n",
        encoding="utf-8",
    )
    try:
        result = _run_import_linter()
    finally:
        probe.unlink(missing_ok=True)

    assert result.returncode != 0, (
        "import-linter accepted a forbidden inventory -> location import. "
        f"The contracts are not enforcing SRS §6.4.\n{result.stdout}"
    )
    assert "inventory depends only on catalogue BROKEN" in result.stdout


def test_reaching_into_another_modules_models_is_rejected() -> None:
    """SRS §6.5 rule 1: cross-module imports only from services and dto.

    `booking` legitimately depends on `trip`, so the dependency contract
    permits the edge. Reaching for trip's *models* rather than its service
    interface must still be refused.
    """
    probe = API_ROOT / "apps" / "booking" / "_arch_probe.py"
    probe.write_text(
        "# Temporary file written by tests/test_architecture.py.\n"
        "from apps.trip import models  # noqa: F401\n",
        encoding="utf-8",
    )
    try:
        result = _run_import_linter()
    finally:
        probe.unlink(missing_ok=True)

    assert result.returncode != 0, (
        "import-linter accepted booking reaching into trip.models. "
        f"SRS §6.5 rule 1 is not enforced.\n{result.stdout}"
    )
    assert "trip internals are private BROKEN" in result.stdout


def test_domain_layer_rejects_django() -> None:
    """SRS §8.2, §36.2: the domain layer is pure.

    This is the contract protecting pricing, cancellation policy and
    itinerary validation from acquiring a database dependency.
    """
    probe = API_ROOT / "apps" / "booking" / "domain" / "_arch_probe.py"
    probe.write_text(
        "# Temporary file written by tests/test_architecture.py.\n"
        "from django.db import models  # noqa: F401\n",
        encoding="utf-8",
    )
    try:
        result = _run_import_linter()
    finally:
        probe.unlink(missing_ok=True)

    assert (
        result.returncode != 0
    ), f"import-linter accepted an ORM import inside a domain layer.\n{result.stdout}"
    assert "domain layers import no ORM, no I/O and no other layer BROKEN" in result.stdout


@pytest.mark.parametrize("module_name", MODULES)
def test_services_never_return_orm_instances(module_name: str) -> None:
    """SRS §6.5 rule 5.

    "A test asserts that every module's services.py exposes only DTOs and
    primitives — never ORM instances — across module boundaries."

    Passes vacuously while the modules are skeletons, and starts constraining
    the moment the first service function is written — which is the point of
    landing it in Phase 1 rather than discovering the rule in Phase 4.
    """
    from django.db.models import Model, QuerySet

    services = importlib.import_module(f"apps.{module_name}.services")

    offenders: list[str] = []
    for name, obj in vars(services).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        if obj.__module__ != services.__name__:
            continue  # re-exported from elsewhere

        try:
            hints = inspect.get_annotations(obj, eval_str=True)
        except Exception:  # an unresolvable hint is not this test's business
            continue

        returned = hints.get("return")
        for candidate in _unwrap(returned):
            if isinstance(candidate, type) and issubclass(candidate, Model | QuerySet):
                offenders.append(f"{name} -> {candidate.__name__}")

    assert not offenders, (
        f"apps.{module_name}.services returns ORM objects across the module "
        f"boundary: {offenders}. Return a DTO from dto.py instead."
    )


def _unwrap(annotation: object) -> list[object]:
    """Flatten Optional[X], list[X], X | Y into their constituent types."""
    import typing

    if annotation is None:
        return []
    args = typing.get_args(annotation)
    if not args:
        return [annotation]
    out: list[object] = []
    for arg in args:
        out.extend(_unwrap(arg))
    return out
