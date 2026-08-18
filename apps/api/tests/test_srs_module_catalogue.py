"""The import-linter contracts are derived from SRS §6.4. This proves it.

Thirty-one contracts encode a table that was originally transcribed from a
`pdftotext` extraction — and `pdftotext -layout` column-scrambles multi-column
tables, including this one. A silent transcription error would not fail any
test; it would simply permit an import the SRS forbids, or forbid one it
allows, and the boundary would rot without anyone noticing.

So the table is re-derived here from `docs/srs/srs-docx.txt`, which is produced
by `scripts/extract_srs.py` reading the OOXML table grid directly. A cell is a
cell there, so the derivation is lossless. The tests then assert that
`.importlinter` says exactly what §6.4 says.

Two deliberate divergences are asserted *as divergences*, so that neither can
drift back without a test failing and a human reading why:

* **C3** — §6.4 names the module `admin`; the codebase calls it
  `administration`, because `admin` collides with `django.contrib.admin`.
* **S3** — §6.4 gives payment's dependency as "booking (via events)", which is
  not implementable as written (§9.4.7 reads trip state synchronously and
  §20.8 writes booking and inventory inside the webhook transaction).
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRS_TEXT = REPO_ROOT / "docs" / "srs" / "srs-docx.txt"
IMPORTLINTER = REPO_ROOT / "apps" / "api" / ".importlinter"

#: §6.4 names this module `admin`; the codebase calls it `administration` (C3).
SRS_TO_CODE = {"admin": "administration"}

#: Every module, in the topological order derived in the implementation plan.
ALL_MODULES = (
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
)

#: What the code implements where it knowingly departs from §6.4 (S3). The
#: value is the *reason*, so a future reader gets it from the failure output.
IMPLEMENTED_DIVERGENCES = {
    "payment": (
        frozenset({"booking", "trip", "inventory"}),
        "SRS §6.4 says 'booking (via events)'. §9.4.7 asserts trip.status and reads "
        "trip.total_amount synchronously, and §20.8 commits holds and confirms bookings "
        "inside the webhook transaction. Issue S3 — revisit at Phase 8.",
    ),
}


def _parse_srs_catalogue() -> dict[str, frozenset[str]]:
    """Re-derive the §6.4 dependency table from the lossless docx extraction.

    Returns {code module name: frozenset of modules it may depend on}.
    """
    if not SRS_TEXT.exists():  # pragma: no cover - guarded by its own test
        pytest.skip(f"{SRS_TEXT} missing; run scripts/extract_srs.py")

    text = SRS_TEXT.read_text(encoding="utf-8")
    start = text.index("6.4\tModule Catalogue")
    end = text.index("6.5\tEnforcing Module Boundaries")
    section = text[start:end]

    known = set(ALL_MODULES) | set(SRS_TO_CODE)
    catalogue: dict[str, frozenset[str]] = {}

    for line in section.splitlines():
        # Table rows are "module | owns | interface | depends"; the identity row
        # sits outside the table as tab-separated text, so accept both.
        cells = [c.strip() for c in re.split(r"\||\t", line) if c.strip()]
        if len(cells) < 4:
            continue
        name = cells[0]
        if name not in known:
            continue
        depends_cell = cells[-1]
        # "— (external port)", "— (external ports)" and "—" all mean no module
        # dependency. "all (read via interfaces)" is administration's grant.
        if depends_cell.startswith("—"):
            depends: frozenset[str] = frozenset()
        elif depends_cell.startswith("all"):
            depends = frozenset(ALL_MODULES) - {SRS_TO_CODE.get(name, name)}
        else:
            # Strip parentheticals such as "booking (via events)".
            raw = re.sub(r"\([^)]*\)", "", depends_cell)
            depends = frozenset(
                SRS_TO_CODE.get(d.strip(), d.strip()) for d in raw.split(",") if d.strip()
            )
        catalogue[SRS_TO_CODE.get(name, name)] = depends

    return catalogue


def _parse_importlinter_deps() -> dict[str, frozenset[str]]:
    """Read back what the contracts actually permit, per module."""
    parser = configparser.ConfigParser(comment_prefixes=(";", "#"), strict=False)
    parser.read(IMPORTLINTER, encoding="utf-8")

    permitted: dict[str, frozenset[str]] = {}
    for section in parser.sections():
        if not section.startswith("importlinter:contract:deps-"):
            continue
        module = section.rsplit("-", 1)[1]
        forbidden = {
            line.strip().removeprefix("apps.")
            for line in parser[section]["forbidden_modules"].splitlines()
            if line.strip()
        }
        permitted[module] = frozenset(ALL_MODULES) - forbidden - {module}
    return permitted


SRS_CATALOGUE = _parse_srs_catalogue()
CONTRACT_DEPS = _parse_importlinter_deps()


def test_every_module_appears_in_the_srs_catalogue() -> None:
    """A module missing from the derivation means the parse silently failed."""
    assert set(SRS_CATALOGUE) == set(ALL_MODULES), (
        "re-derived §6.4 does not cover every module; "
        f"missing {set(ALL_MODULES) - set(SRS_CATALOGUE)}, "
        f"unexpected {set(SRS_CATALOGUE) - set(ALL_MODULES)}"
    )


def test_administration_has_no_dependency_contract() -> None:
    """§6.4 grants administration 'all (read via interfaces)'.

    A `deps-administration` contract would therefore forbid nothing, so it is
    deliberately absent. The `private-*` contracts still constrain *how* it
    reads — through services and dto, never another module's models.
    """
    assert SRS_CATALOGUE["administration"] == frozenset(ALL_MODULES) - {"administration"}
    assert "administration" not in CONTRACT_DEPS


@pytest.mark.parametrize("module", [m for m in ALL_MODULES if m != "administration"])
def test_contract_matches_the_srs_dependency_table(module: str) -> None:
    """Each deps-* contract permits exactly what §6.4 allows."""
    expected = SRS_CATALOGUE[module]
    if module in IMPLEMENTED_DIVERGENCES:
        expected, reason = IMPLEMENTED_DIVERGENCES[module]
        assert SRS_CATALOGUE[module] != expected, (
            f"{module} is registered as a divergence from §6.4 but now matches it. "
            f"Remove it from IMPLEMENTED_DIVERGENCES. Recorded reason: {reason}"
        )

    assert module in CONTRACT_DEPS, f"no deps-{module} contract in .importlinter"
    assert CONTRACT_DEPS[module] == expected, (
        f"deps-{module} permits {sorted(CONTRACT_DEPS[module])} "
        f"but SRS §6.4 allows {sorted(expected)}"
    )


def test_the_dependency_graph_is_acyclic() -> None:
    """§6.2 requires seams that can be extracted later. A cycle destroys that.

    `administration` is excluded: its "all (read via interfaces)" grant is a
    read-only edge through service interfaces, not a structural dependency, and
    including it would make the graph trivially cyclic (issue S1).
    """
    graph = {m: SRS_CATALOGUE[m] for m in ALL_MODULES if m != "administration"}
    resolved: set[str] = set()
    while True:
        ready = {m for m, deps in graph.items() if m not in resolved and deps <= resolved}
        if not ready:
            break
        resolved |= ready
    unresolved = set(graph) - resolved
    assert not unresolved, f"§6.4 dependency graph is cyclic among {sorted(unresolved)}"
