"""Check 2 of SRS §30.3 — the ownership predicate, applied to the queryset.

    OWNERSHIP CHECK does this principal own, or have a granted relationship
                    to, this specific row?
                    (queryset filtered by principal at the repository layer —
                     never "fetch then compare", which leaks existence)

A view that exposes rows declares `ownership_resource` and inherits
`ScopedQuerysetMixin`. Everything else follows: `get_object()` runs against a
queryset whose only members are the principal's rows, so a foreign
`public_id` raises `Http404` on its own and there is no branch anywhere that
could return 403 instead.

`tests/test_authorisation_matrix.py` enumerates the URL conf and fails the
build for any row-exposing view that does not declare one.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.db.models import QuerySet
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.common.authentication import principal_from_request
from apps.common.authz import Resource
from apps.common.permissions import deny_object_permission
from apps.common.scoping import scoped

__all__ = ["ScopedQuerysetMixin", "NoObjectPermissionMixin", "ImproperlyScopedViewError"]

#: The no-op DRF ships. Anything else means a permission class has defined an
#: object-level hook, which this project does not use.
_BASE_HAS_OBJECT_PERMISSION = BasePermission.has_object_permission


class ImproperlyScopedViewError(RuntimeError):
    """A row-exposing view did not say whose rows they are.

    Fatal rather than defaulting. Denying everything by default looks like a
    bug and gets "fixed" by removing the scoping; allowing everything by
    default needs no explanation.
    """


class NoObjectPermissionMixin:
    """Make `has_object_permission` a loud failure.

    §30.3 rules out comparing after the fetch, so no permission class in this
    project may define an object-level hook. A convention that relies on
    memory is not a control, so reintroducing one fails here in development
    rather than regressing quietly in production.
    """

    def check_object_permissions(self, request: Any, obj: Any) -> None:
        for permission in self.get_permissions():  # type: ignore[attr-defined]
            if type(permission).has_object_permission is not _BASE_HAS_OBJECT_PERMISSION:
                deny_object_permission()


class ScopedQuerysetMixin(NoObjectPermissionMixin):
    """Restrict every queryset this view produces to the principal's rows."""

    #: Which §5.2 ownership rules apply. Required — there is no default,
    #: because a default would be a guess about who may see these rows.
    ownership_resource: ClassVar[Resource]

    def get_queryset(self) -> QuerySet[Any]:
        queryset = super().get_queryset()  # type: ignore[misc]
        resource = getattr(self, "ownership_resource", None)
        if resource is None:
            raise ImproperlyScopedViewError(
                f"{type(self).__name__} uses ScopedQuerysetMixin without declaring "
                "`ownership_resource`. Declare it, or do not expose rows from this view."
            )
        request = getattr(self, "request", None)
        return scoped(
            queryset,
            principal_from_request(request),
            resource,
            write=getattr(request, "method", "GET") not in SAFE_METHODS,
        )
