"""The authorisation harness of SRS §37.2 and §30.3.

Three things, all asserted by *enumerating the URL conf* rather than by
inspection, because the failure mode being guarded against is an endpoint
somebody forgot about:

1.  **No endpoint is unintentionally public.** Every route either requires a
    principal or appears on an explicit allow-list with a stated reason.
2.  **Every row-exposing view declares its ownership resource.** A view that
    filters nothing is the OWASP API #1 defect this project's whole
    authorisation design exists to prevent.
3.  **A foreign principal receives 404, not 403** — because 403 confirms
    existence (§30.3).
4.  **The exemptions cannot be abused.** Both allow-lists below are themselves
    checked: a route may only sit on the cheap one if the URL conf and the
    view's own source prove it identifies no row.

The matrix grows on its own as later phases add endpoints. That is the point:
§37.2 requires it to run green "across every endpoint then existing", so the
list of endpoints must not be maintained by hand.

**Why the fourth property exists.** An exemption list rots. Some phases from
now somebody hits a matrix failure, sees a list of route names that make
failures go away, and adds theirs — and a scoping control has quietly become
optional without one line of it changing. So `NO_ROWS_EXPOSED` is not a list of
names a reviewer trusted; it is a list of names the build re-proves on every
run. A route with a path parameter, or a view whose handlers call
`get_object()`, cannot be exempted there at all, whatever reason is typed
beside it.

Routes that genuinely do resolve a caller-supplied identifier, but through a
principal-scoped selector rather than through `ScopedQuerysetMixin`, go on
`SCOPED_BY_A_SELECTOR` instead. That one cannot be proven mechanically — the
proof is in the selector — so it is guarded from the other direction: nothing
create-shaped may hide on it, because anything that could be proven belongs
where it is.
"""

from __future__ import annotations

import inspect
import re
import uuid
from typing import Any

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.permissions import AllowAny
from rest_framework.test import APIClient

from apps.common.authz import Resource
from apps.common.mixins import ScopedQuerysetMixin
from apps.common.permissions import IsAuthenticatedPrincipal
from apps.identity import repositories as repo
from apps.identity import services

pytestmark = pytest.mark.django_db

#: Routes that are public *by design*, each with the reason it must be.
#: Adding a name here is a deliberate act and shows up in review.
PUBLIC_BY_DESIGN = {
    "v1:common:health": "Liveness probe; discloses no data (SRS §35.6).",
    "v1:identity:register": "Account creation — there is no principal yet (§9.4.1).",
    "v1:identity:verify-email": "Consumes an emailed token; the token is the credential.",
    "v1:identity:login": "Issues the credential (§9.4.2).",
    "v1:identity:refresh": "Presents a refresh token; the token is the credential.",
    "v1:identity:password-forgot": "Unauthenticated by necessity (§24.5).",
    "v1:identity:password-reset": "Consumes an emailed token; the token is the credential.",
    "schema": "OpenAPI document (§36.2).",
    "swagger-ui": "Renders the OpenAPI document.",
}

#: Views that identify no row and therefore need no ownership rule.
#:
#: Adding a name here does *not* make a matrix failure go away on its own.
#: `TestTheRowlessExemptionCannotBeAbused` re-proves every entry: the route
#: must take no path parameter, and no handler on the view may call
#: `get_object()`. A route that resolves a caller-supplied identifier cannot be
#: exempted here whatever reason is written beside it — see
#: `SCOPED_BY_A_SELECTOR` below.
NO_ROWS_EXPOSED = {
    "v1:identity:me": "Selects the caller's own row by principal, not by a supplied id.",
    "v1:identity:mfa-enrol": "Acts on the caller's own account only.",
    "v1:identity:mfa-verify": "Acts on the caller's own account only.",
    "v1:identity:logout": "Acts on the caller's own sessions only.",
    "v1:identity:device-list": "Lists by principal; there is no id to supply.",
    "v1:common:health": "No data.",
    # The §27.8 catalogue console's create endpoints. A POST to a collection
    # looks up no row, so there is nothing for an ownership predicate to
    # filter: what stands between a caller and a new curated row is the role
    # check, `HasPermission.for_(Permission.CATALOGUE_MANAGE)`. Listed one per
    # entity rather than matched by prefix, so that adding an eighth curated
    # table is a line in this file that a reviewer sees.
    "v1:catalogue:admin-country-create": "Creates a row; looks none up.",
    "v1:catalogue:admin-region-create": "Creates a row; looks none up.",
    "v1:catalogue:admin-destination-create": "Creates a row; looks none up.",
    "v1:catalogue:admin-tag-create": "Creates a row; looks none up.",
    "v1:catalogue:admin-attraction-create": "Creates a row; looks none up.",
    "v1:catalogue:admin-activity-create": "Creates a row; looks none up.",
    "v1:catalogue:admin-accommodation-create": "Creates a row; looks none up.",
}

#: Views that *do* resolve a caller-supplied identifier, but filter by
#: principal in a selector rather than through `ScopedQuerysetMixin`.
#:
#: The §30.3 property still holds — the filter is applied before the row is
#: fetched, so a foreign identifier is never loaded — but no static check can
#: see that, because the proof is inside the selector. So this list is guarded
#: from the opposite direction: every entry must take a path parameter, which
#: means nothing that *could* have been proven is allowed to hide here. Each
#: reason names the scoped selector, so the claim can be followed rather than
#: taken.
SCOPED_BY_A_SELECTOR = {
    "v1:identity:device-detail": (
        "`services.remove_device` resolves it through `selectors.devices_visible_to`, "
        "which applies the principal filter before the row is fetched."
    ),
}

#: Both allow-lists, for the checks that do not care which one a name is on.
EXEMPT = {**NO_ROWS_EXPOSED, **SCOPED_BY_A_SELECTOR}


def _walk(patterns: Any, prefix: str = "", namespace: str = "") -> list[tuple[str, str, Any]]:
    """Every named route, with the path it is mounted at.

    The path is carried rather than discarded because the exemption guard
    reads it: a `<uuid:public_id>` in the URL is the cheapest possible proof
    that a view resolves a caller-supplied identifier.
    """
    found: list[tuple[str, str, Any]] = []
    for entry in patterns:
        if isinstance(entry, URLResolver):
            ns = entry.namespace or ""
            child_ns = f"{namespace}:{ns}" if namespace and ns else (ns or namespace)
            found += _walk(entry.url_patterns, prefix + str(entry.pattern), child_ns)
        elif isinstance(entry, URLPattern):
            name = entry.name or ""
            full = f"{namespace}:{name}" if namespace else name
            found.append((full, prefix + str(entry.pattern), entry.callback))
    return found


ROUTES = _walk(get_resolver().url_patterns)


def _view_class(callback: Any) -> Any:
    return getattr(callback, "cls", getattr(callback, "view_class", None))


ROUTE_VIEWS = [(name, _view_class(cb)) for name, _, cb in ROUTES if _view_class(cb) is not None]
ROUTE_PATHS = {name: path for name, path, _ in ROUTES}

#: A `path()` converter (`<uuid:public_id>`) or a `re_path` named group. Either
#: is a value the caller chose that the view must resolve to a row.
_PATH_PARAMETER = re.compile(r"<[^>]+>|\(\?P<")

#: The handlers whose bodies are the view's own work. `options` is DRF's and
#: `head` is derived from `get`, so neither says anything about this view.
_HANDLERS = ("get", "post", "put", "patch", "delete")

#: How a DRF view fetches one row. `get_object` is the generic path;
#: `get_object_or_404` is the one people reach for when writing it by hand.
_LOOKUPS = ("get_object", "get_object_or_404")


def _handler_source(view: Any) -> dict[str, str]:
    """The source of every HTTP handler this view answers with.

    Resolved through the MRO rather than `view.__dict__`, because the catalogue
    console's twenty-one classes inherit their handlers from three shared
    bases — reading only the subclass body would find nothing at all and pass
    every check by accident.
    """
    sources: dict[str, str] = {}
    for method in _HANDLERS:
        handler = getattr(view, method, None)
        if handler is None:
            continue
        try:
            sources[method] = inspect.getsource(handler)
        except (OSError, TypeError):  # pragma: no cover - C or dynamic handler
            sources[method] = ""
    return sources


class TestTheEnumerationItself:
    def test_routes_were_actually_found(self) -> None:
        """A walker that silently returns nothing would pass every test
        below while checking absolutely nothing."""
        assert len(ROUTE_VIEWS) >= 13

    def test_every_route_has_a_name(self) -> None:
        """An unnamed route cannot be allow-listed, reversed, or reviewed."""
        unnamed = [name for name, _ in ROUTE_VIEWS if not name or name.endswith(":")]
        assert not unnamed, f"unnamed routes: {unnamed}"


class TestNoEndpointIsUnintentionallyPublic:
    """SRS §37.2 acceptance criterion, and §30.6."""

    @pytest.mark.parametrize(("name", "view"), ROUTE_VIEWS, ids=[n for n, _ in ROUTE_VIEWS])
    def test_route_requires_a_principal_or_is_listed(self, name: str, view: Any) -> None:
        permissions = list(getattr(view, "permission_classes", []))
        is_open = not permissions or any(p is AllowAny for p in permissions)
        if is_open:
            assert name in PUBLIC_BY_DESIGN, (
                f"{name} is publicly reachable and is not on the allow-list. "
                "Add a permission class, or add it to PUBLIC_BY_DESIGN with the "
                "reason it must be public."
            )
        else:
            assert any(
                issubclass(p, IsAuthenticatedPrincipal) or p is not AllowAny for p in permissions
            )

    def test_the_allow_list_has_no_stale_entries(self) -> None:
        """A removed route left on the list quietly widens the next one that
        happens to reuse the name."""
        names = {name for name, _ in ROUTE_VIEWS}
        stale = set(PUBLIC_BY_DESIGN) - names
        assert not stale, f"PUBLIC_BY_DESIGN names routes that no longer exist: {stale}"

    def test_every_allow_list_entry_states_a_reason(self) -> None:
        for name, reason in PUBLIC_BY_DESIGN.items():
            assert len(reason) > 20, f"{name} has no real justification"


class TestEveryRowExposingViewIsScoped:
    @pytest.mark.parametrize(("name", "view"), ROUTE_VIEWS, ids=[n for n, _ in ROUTE_VIEWS])
    def test_scoped_or_explicitly_rowless(self, name: str, view: Any) -> None:
        if issubclass(view, ScopedQuerysetMixin):
            assert (
                getattr(view, "ownership_resource", None) is not None
            ), f"{name} uses ScopedQuerysetMixin without declaring ownership_resource"
            return
        if name in PUBLIC_BY_DESIGN or name in EXEMPT:
            return
        pytest.fail(
            f"{name} neither scopes its queryset nor is listed in NO_ROWS_EXPOSED. "
            "Filter by principal (SRS §30.3), or record why it exposes no rows."
        )

    def test_the_rowless_list_has_no_stale_entries(self) -> None:
        names = {name for name, _ in ROUTE_VIEWS}
        assert not set(EXEMPT) - names

    def test_a_route_is_not_on_both_lists(self) -> None:
        """They mean different things and are checked differently. A name on
        both would be exempted by whichever check happened to be weaker."""
        assert not set(NO_ROWS_EXPOSED) & set(SCOPED_BY_A_SELECTOR)


class TestTheRowlessExemptionCannotBeAbused:
    """The exemption list is re-proven, not trusted — see the module docstring.

    Both checks are deliberately cheap and blunt. A subtle check that could be
    argued with is a check somebody argues with at 5pm; these two can only be
    satisfied by the route actually being what it claims to be.
    """

    @pytest.mark.parametrize("name", sorted(NO_ROWS_EXPOSED))
    def test_an_exempt_route_accepts_no_row_identifier(self, name: str) -> None:
        """A path parameter is a caller-supplied value the view must resolve.

        There is no such thing as a route that takes one and exposes no rows,
        so this is the check that stops the list absorbing a detail endpoint
        the day somebody is in a hurry.
        """
        path = ROUTE_PATHS.get(name)
        assert path is not None, f"{name} is on NO_ROWS_EXPOSED but is not a route"
        assert not _PATH_PARAMETER.search(path), (
            f"{name} is exempted as row-less but its path is {path!r}, which takes an "
            "identifier. Scope it with ScopedQuerysetMixin, or — if it resolves that "
            "identifier through a principal-scoped selector — move it to "
            "SCOPED_BY_A_SELECTOR and name the selector."
        )

    @pytest.mark.parametrize("name", sorted(NO_ROWS_EXPOSED))
    def test_an_exempt_view_fetches_no_row(self, name: str) -> None:
        """No handler may call `get_object()` or `get_object_or_404()`.

        The catalogue console's create views inherit from `GenericAPIView`, so
        `get_object` *exists* on them; what matters is that nothing calls it.
        Reading the handler bodies is what tells those apart, and it is why
        this reads source rather than `hasattr`.
        """
        view = dict(ROUTE_VIEWS).get(name)
        assert view is not None, f"{name} is on NO_ROWS_EXPOSED but has no view class"
        offenders = [
            f"{method}() calls {lookup}"
            for method, source in _handler_source(view).items()
            for lookup in _LOOKUPS
            if f"{lookup}(" in source
        ]
        assert not offenders, (
            f"{name} is exempted as row-less but {'; '.join(offenders)}. A view that "
            "fetches a row must filter the queryset by principal first (SRS §30.3)."
        )

    @pytest.mark.parametrize("name", sorted(SCOPED_BY_A_SELECTOR))
    def test_the_selector_list_takes_only_routes_that_resolve_an_identifier(
        self, name: str
    ) -> None:
        """Guarded from the other side.

        This list cannot be proven mechanically — the proof is inside the
        selector — so nothing is allowed on it that *could* have been proven.
        A create-shaped route belongs on `NO_ROWS_EXPOSED`, where the build
        checks it on every run rather than a reviewer checking it once.
        """
        path = ROUTE_PATHS.get(name)
        assert path is not None, f"{name} is on SCOPED_BY_A_SELECTOR but is not a route"
        assert _PATH_PARAMETER.search(path), (
            f"{name} takes no path parameter, so it resolves no identifier and needs no "
            "selector to scope one. Move it to NO_ROWS_EXPOSED, where it is checked."
        )

    @pytest.mark.parametrize("name", sorted(SCOPED_BY_A_SELECTOR))
    def test_the_selector_list_names_the_selector(self, name: str) -> None:
        """A reason that does not say which selector is a reason nobody can
        follow, which makes it indistinguishable from no reason."""
        reason = SCOPED_BY_A_SELECTOR[name]
        assert "selector" in reason and "`" in reason, f"{name}: {reason!r}"

    def test_the_guard_would_notice_a_detail_route_being_added(self) -> None:
        """The guard tested against itself.

        Both checks above pass vacuously if the walker stops carrying paths or
        `_PATH_PARAMETER` stops matching, and the failure mode of a broken
        guard is a green build. So: a route that genuinely takes an identifier
        must be rejected by the same predicate the exemption test uses.
        """
        detail_routes = [name for name, path in ROUTE_PATHS.items() if _PATH_PARAMETER.search(path)]
        assert detail_routes, "no route in the URL conf takes a path parameter"
        assert not set(detail_routes) & set(NO_ROWS_EXPOSED)


class TestOwnershipRulesCoverEveryResource:
    def test_every_resource_is_reachable_from_some_rule(self) -> None:
        """Handled exhaustively in the domain tests; asserted here too so a
        new Resource cannot be added without the matrix noticing."""
        from apps.common.authz import OWNERSHIP, Role

        for resource in Resource:
            assert any((role, resource) in OWNERSHIP for role in Role)


# ---------------------------------------------------------------------------
# The behavioural half: a foreign principal gets 404.
# ---------------------------------------------------------------------------

PASSWORD = "a-perfectly-fine-passphrase"


def make_user(email: str) -> Any:
    services.register_tourist(email=email, password=PASSWORD, first_name="Test", last_name="User")
    user = repo.find_user_by_email(email)
    assert user is not None
    from django.utils import timezone

    repo.mark_email_verified(user, now=timezone.now())
    user.refresh_from_db()
    return user


def client_for(user: Any) -> APIClient:
    result = services.authenticate(email=user.email, password=PASSWORD)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {result.tokens.access_token}")
    return client


class TestForeignPrincipalGets404:
    """§30.3: "another principal receives 404, not 403, for rows they do not
    own — because 403 confirms existence"."""

    def test_a_foreign_device_is_not_found(self) -> None:
        owner = make_user("owner@example.com")
        stranger = make_user("stranger@example.com")

        created = client_for(owner).post(
            "/api/v1/me/devices",
            {"platform": "IOS", "push_token": "tok-owner"},
            format="json",
        )
        assert created.status_code == 201
        public_id = created.json()["data"]["public_id"]

        response = client_for(stranger).delete(f"/api/v1/me/devices/{public_id}")
        assert response.status_code == 404, "a foreign row must not return 403"

    def test_the_owner_can_delete_their_own(self) -> None:
        owner = make_user("owner@example.com")
        client = client_for(owner)
        created = client.post(
            "/api/v1/me/devices", {"platform": "IOS", "push_token": "tok-owner"}, format="json"
        )
        public_id = created.json()["data"]["public_id"]
        assert client.delete(f"/api/v1/me/devices/{public_id}").status_code == 204

    def test_a_nonexistent_device_is_indistinguishable_from_a_foreign_one(self) -> None:
        owner = make_user("owner@example.com")
        stranger = make_user("stranger@example.com")
        client = client_for(owner)
        created = client.post(
            "/api/v1/me/devices", {"platform": "IOS", "push_token": "tok-owner"}, format="json"
        )
        public_id = created.json()["data"]["public_id"]

        foreign = client_for(stranger).delete(f"/api/v1/me/devices/{public_id}")
        missing = client_for(stranger).delete(f"/api/v1/me/devices/{uuid.uuid4()}")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["error"]["code"] == missing.json()["error"]["code"]

    def test_a_stranger_never_sees_another_principals_devices(self) -> None:
        owner = make_user("owner@example.com")
        stranger = make_user("stranger@example.com")
        client_for(owner).post(
            "/api/v1/me/devices", {"platform": "IOS", "push_token": "tok-owner"}, format="json"
        )
        listing = client_for(stranger).get("/api/v1/me/devices")
        assert listing.json()["data"] == []


class TestAuthenticationIsRequired:
    @pytest.mark.parametrize(
        "path",
        ["/api/v1/me", "/api/v1/me/devices", "/api/v1/auth/logout", "/api/v1/auth/mfa/enrol"],
    )
    def test_an_anonymous_request_is_refused(self, path: str) -> None:
        assert APIClient().get(path).status_code in (401, 405)

    def test_a_forged_token_is_refused(self) -> None:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        assert client.get("/api/v1/me").status_code == 401
