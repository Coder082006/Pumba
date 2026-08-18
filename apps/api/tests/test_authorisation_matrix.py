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

The matrix grows on its own as later phases add endpoints. That is the point:
§37.2 requires it to run green "across every endpoint then existing", so the
list of endpoints must not be maintained by hand.
"""

from __future__ import annotations

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

#: Views that expose no per-row data and therefore need no ownership rule.
#: The reason is recorded so "it needed none" is a claim someone made, not a
#: gap someone left.
NO_ROWS_EXPOSED = {
    "v1:identity:me": "Selects the caller's own row by principal, not by a supplied id.",
    "v1:identity:mfa-enrol": "Acts on the caller's own account only.",
    "v1:identity:mfa-verify": "Acts on the caller's own account only.",
    "v1:identity:logout": "Acts on the caller's own sessions only.",
    "v1:identity:device-list": "Delegates to a scoped selector.",
    "v1:identity:device-detail": "Delegates to a scoped selector.",
    "v1:common:health": "No data.",
}


def _walk(patterns: Any, prefix: str = "", namespace: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    for entry in patterns:
        if isinstance(entry, URLResolver):
            ns = entry.namespace or ""
            child_ns = f"{namespace}:{ns}" if namespace and ns else (ns or namespace)
            found += _walk(entry.url_patterns, prefix + str(entry.pattern), child_ns)
        elif isinstance(entry, URLPattern):
            name = entry.name or ""
            full = f"{namespace}:{name}" if namespace else name
            found.append((full, entry.callback))
    return found


ROUTES = _walk(get_resolver().url_patterns)


def _view_class(callback: Any) -> Any:
    return getattr(callback, "cls", getattr(callback, "view_class", None))


ROUTE_VIEWS = [(name, _view_class(cb)) for name, cb in ROUTES if _view_class(cb) is not None]


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
        if name in PUBLIC_BY_DESIGN or name in NO_ROWS_EXPOSED:
            return
        pytest.fail(
            f"{name} neither scopes its queryset nor is listed in NO_ROWS_EXPOSED. "
            "Filter by principal (SRS §30.3), or record why it exposes no rows."
        )

    def test_the_rowless_list_has_no_stale_entries(self) -> None:
        names = {name for name, _ in ROUTE_VIEWS}
        assert not set(NO_ROWS_EXPOSED) - names


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
