"""§41.12 — destination independence, as a formal acceptance test. Validates OBJ-6.

    An administrator, using only the admin console and the seed loader,
    creates a new country, region and destination (for example Arusha), adds
    one attraction, one activity, one accommodation with a room type and
    calendar, and one transfer corridor with a tariff. A tourist can then
    plan, quote, pay for and complete a trip to that destination. Pass
    condition: no application code change, no deployment, and no database
    migration is required at any point.

This file is the Phase 3 half of that criterion: country, region, destination
and attraction, created and then read back. The trip, quote, payment and
transfer corridor arrive with the phases that build them, and this file grows
to meet them.

**Every catalogue row here is created through the admin write API.** Not a
factory, not `Model.objects.create`, not a fixture. That restriction is the
entire point of §41.12 and it is easy to lose: a fixture-seeded version of this
test passes while proving nothing, because the thing being proved is that an
administrator can open a market *with no engineering involvement*. If the only
way to get a row in is to write Python, the criterion has failed no matter what
the assertions say.

The one thing built outside that rule is the administrator's own account, via
`identity`. That is the actor, not the market: §41.12 grants an administrator
and asks what they can do with the console. Creating the person who logs in is
not the thing under test.

**This test is expected to fail until commit 27.** The admin write API, the
public read endpoints and `/search` are commits 25 to 27; today the first POST
returns 404. It is marked `xfail(strict=True)` rather than skipped or deleted,
which means two things. While the endpoints are missing it reports XFAIL and
the suite stays usable. The moment it passes, `strict=True` turns the build
red — so it cannot go green quietly, and nobody can mistake "the test started
passing" for "somebody weakened the test". Removing the marker is then a
deliberate act that shows up in review.

Written now, before any of the endpoints it calls, because an acceptance test
written after the fact tests what was built. This one states what must be true
and lets the endpoints be shaped to meet it.
"""

from __future__ import annotations

import ast
from typing import Any

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.common.authz import Role
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

#: Arusha, as §41.12 names it. Deliberately mainland Tanzania: the seeded
#: market is Zanzibar, so a destination the seed data never mentions is the
#: only honest test of "no destination-specific code" (CLAUDE.md rule 2, §4.2).
ARUSHA = {
    "name": "Arusha",
    "slug": "arusha",
    "latitude": "-3.3869",
    "longitude": "36.6830",
    "timezone": "Africa/Dar_es_Salaam",
    "default_currency": "TZS",
}

NGORONGORO = {
    "name": "Ngorongoro Crater",
    "slug": "ngorongoro-crater",
    "latitude": "-3.2028",
    "longitude": "35.5872",
}


def _administrator() -> APIClient:
    """A CATALOGUE_ADMIN, signed in. The actor, not the market.

    Includes TOTP enrolment, because §30.2 makes it mandatory for every
    administrative role and `authenticate` refuses to issue a token without
    it. An administrator who could reach the console without a second factor
    would not be the administrator §41.12 is about.
    """
    return signed_in_as(Role.CATALOGUE_ADMIN)


def _created(response: Any, what: str) -> dict[str, Any]:
    """Assert a 201 and return the created resource, or say what broke.

    The message names the endpoint because while this test is red the first
    failure is a 404, and "404 on POST /api/v1/admin/countries" is a more
    useful thing to read than an assertion on a status code.
    """
    assert response.status_code == 201, (
        f"creating the {what} through the admin API returned "
        f"{response.status_code}: {getattr(response, 'data', response.content)!r}"
    )
    return dict(response.data["data"])


@pytest.mark.xfail(
    strict=True,
    reason=(
        "§41.12 acceptance criterion. The admin write API (commit 25), the public "
        "read API (26) and /search (27) do not exist yet, so this cannot pass. "
        "strict=True so that it cannot pass quietly either: if this XPASSes, "
        "either the endpoints have landed and the marker should be removed in a "
        "commit that says so, or the test has been weakened."
    ),
)
class TestAnAdministratorCanOpenANewMarket:
    """The whole criterion as one flow, because that is how it is claimed.

    Split into separate tests it would be possible for four of five to pass and
    for the market still not to be open. §41.12 is a single claim about a single
    sequence, so it is asserted as one.
    """

    def test_arusha_opens_with_no_engineering_involvement(self) -> None:
        admin = _administrator()

        # --- 1. The geography, through the console's API ---------------------
        country = _created(
            admin.post(
                "/api/v1/admin/countries",
                {
                    "iso_code": "TZ",
                    "name": "Tanzania",
                    "default_currency": "TZS",
                    "default_timezone": "Africa/Dar_es_Salaam",
                },
                format="json",
            ),
            "country",
        )
        region = _created(
            admin.post(
                "/api/v1/admin/regions",
                {"country": country["public_id"], "name": "Arusha Region", "slug": "arusha-region"},
                format="json",
            ),
            "region",
        )
        destination = _created(
            admin.post(
                "/api/v1/admin/destinations",
                {"region": region["public_id"], **ARUSHA},
                format="json",
            ),
            "destination",
        )

        # --- 2. A new market is not public until somebody says so ------------
        # §7.5.6 defaults `is_active` to false. Asserted before activation
        # because "it appeared the moment it was created" would be a different
        # and worse property: the console would have no way to stage a market.
        listed = admin.get("/api/v1/destinations")
        assert destination["public_id"] not in _public_ids(listed)

        activated = admin.patch(
            f"/api/v1/admin/destinations/{destination['public_id']}",
            {"is_active": True},
            format="json",
        )
        assert activated.status_code == 200, activated.data

        # --- 3. One attraction in it -----------------------------------------
        attraction = _created(
            admin.post(
                "/api/v1/admin/attractions",
                {"destination": destination["public_id"], **NGORONGORO},
                format="json",
            ),
            "attraction",
        )

        # --- 4. A tourist — unauthenticated — can find all of it -------------
        # §9.3.2's catalogue endpoints are public, so no credentials here on
        # purpose: the market is open to somebody who has never signed in.
        public = APIClient()

        destinations = public.get("/api/v1/destinations")
        assert destinations.status_code == 200
        assert destination["public_id"] in _public_ids(destinations)

        detail = public.get(f"/api/v1/destinations/{destination['public_id']}")
        assert detail.status_code == 200
        assert detail.data["data"]["slug"] == "arusha"
        assert detail.data["data"]["timezone"] == "Africa/Dar_es_Salaam"

        attractions = public.get("/api/v1/attractions", {"destination": "arusha"})
        assert attractions.status_code == 200
        assert attraction["public_id"] in _public_ids(attractions)

        found = public.get("/api/v1/search", {"q": "Ngorongoro"})
        assert found.status_code == 200
        assert attraction["public_id"] in _public_ids(found)

        # --- 5. And it is in the sitemap -------------------------------------
        # `app/sitemap.ts` (commit 34) enumerates exactly these unfiltered list
        # payloads, so a row present in both is a row in the sitemap. Asserting
        # the payloads rather than the rendered XML keeps the criterion in the
        # layer that decides it: if a row is absent here, no amount of Next.js
        # will put it in the sitemap.
        assert destination["public_id"] in _public_ids(public.get("/api/v1/destinations"))
        assert attraction["public_id"] in _public_ids(public.get("/api/v1/attractions"))

    def test_deactivating_the_market_closes_it_again(self) -> None:
        """The other half of "without a deployment".

        A market that can be opened by an administrator and only closed by an
        engineer is not destination-independent; it is destination-independent
        in one direction.
        """
        admin = _administrator()
        country = _created(
            admin.post(
                "/api/v1/admin/countries",
                {
                    "iso_code": "TZ",
                    "name": "Tanzania",
                    "default_currency": "TZS",
                    "default_timezone": "Africa/Dar_es_Salaam",
                },
                format="json",
            ),
            "country",
        )
        region = _created(
            admin.post(
                "/api/v1/admin/regions",
                {"country": country["public_id"], "name": "Arusha Region", "slug": "arusha-region"},
                format="json",
            ),
            "region",
        )
        destination = _created(
            admin.post(
                "/api/v1/admin/destinations",
                {"region": region["public_id"], **ARUSHA, "is_active": True},
                format="json",
            ),
            "destination",
        )
        public = APIClient()
        assert destination["public_id"] in _public_ids(public.get("/api/v1/destinations"))

        admin.patch(
            f"/api/v1/admin/destinations/{destination['public_id']}",
            {"is_active": False},
            format="json",
        )
        assert destination["public_id"] not in _public_ids(public.get("/api/v1/destinations"))
        assert public.get(f"/api/v1/destinations/{destination['public_id']}").status_code == 404


def _public_ids(response: Any) -> set[str]:
    """The `public_id`s in a §9.2 list envelope, whatever it is a list of."""
    if response.status_code != 200:
        return set()
    payload = response.data.get("data", [])
    rows = payload if isinstance(payload, list) else payload.get("items", [])
    return {str(row["public_id"]) for row in rows}


class TestNoMigrationIsRequiredToOpenAMarket:
    """§41.12's pass condition, the half that can be asserted mechanically.

    "No database migration is required at any point" is a claim about the
    schema, and it is checkable today — long before the endpoints exist —
    because it is a property of the models rather than of the API. It is
    therefore *not* under the `xfail` above: it passes now and must keep
    passing, and if it ever fails, some later phase has made a market's
    existence depend on DDL.
    """

    def test_the_model_state_and_the_migrations_agree(self) -> None:
        """The same check CI runs, asserted here with §41.12's reason attached.

        A new destination is rows. If `makemigrations` ever finds something to
        write, then opening a market has acquired a schema change and OBJ-6 is
        broken regardless of what the API can do.
        """
        try:
            call_command("makemigrations", check=True, dry_run=True, verbosity=0)
        except SystemExit as exit_code:  # pragma: no cover - only on failure
            pytest.fail(
                "makemigrations found unwritten changes, so the schema and the models "
                f"disagree (exit {exit_code.code}). §41.12 requires that opening a new "
                "destination needs no migration; that guarantee starts with the schema "
                "already matching the code."
            )

    def test_no_module_branches_on_a_destination_name(self) -> None:
        """CLAUDE.md rule 2 and §4.2, which is what makes §41.12 achievable.

        Arusha only works if nothing anywhere *branches* on "Zanzibar". The
        distinction that matters is between a name in prose and a name in code:
        a docstring explaining that Zanzibar is the first configured market is
        the documentation working, while `if destination.name == "Zanzibar"` is
        the defect. So this reads the AST and ignores comments — which never
        reach it — and docstrings, which it identifies and skips explicitly.

        Every other string literal counts, including one sitting in a default,
        a lookup table or a constant, because each of those is a branch waiting
        to be written.

        This is the cheap, always-on half of TC-901's manual review, and it runs
        over the whole application tree rather than a sampled list of files.
        """
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        markers = ("zanzibar", "unguja", "pemba", "nungwi", "stone town")
        offenders: list[str] = []

        for path in sorted(root.rglob("*.py")):
            if {"migrations", "tests", "__pycache__", ".venv"} & set(path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = _docstring_nodes(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                lowered = node.value.lower()
                for marker in markers:
                    if marker in lowered:
                        offenders.append(f"{path.relative_to(root)}:{node.lineno} {node.value!r}")

        assert not offenders, (
            "a destination name appears as a string literal in application code, "
            "which §4.2 forbids and §41.12 would fail on. Destinations are data: "
            f"{offenders}"
        )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of every module, class and function docstring node.

    Skipped rather than scanned, because prose naming the first configured
    market is the documentation doing its job. Comments need no handling: the
    AST never sees them.
    """
    import ast as _ast

    found: set[int] = set()
    for node in _ast.walk(tree):
        if not isinstance(
            node, _ast.Module | _ast.ClassDef | _ast.FunctionDef | _ast.AsyncFunctionDef
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], _ast.Expr)
            and isinstance(body[0].value, _ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))
    return found
