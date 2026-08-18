"""Turn an ownership `Filter` into a queryset restriction — SRS §30.3.

    "queryset filtered by principal at the repository layer — never
     'fetch then compare', which leaks existence"

This is the only module in the codebase that builds an authorisation `Q`.
Everything upstream of it is a pure decision over value objects; everything
downstream is an ordinary queryset. Keeping the translation in one function
means the 404-not-403 property is a property of the system rather than of
fourteen correct implementations.

`DENY_ALL` becomes `queryset.none()`. That is deliberate and load-bearing: a
`get_object()` against an empty queryset raises `Http404`, so a principal who
does not own a row is told the row does not exist, per §30.3 — "because 403
confirms existence".
"""

from __future__ import annotations

from typing import TypeVar

from django.db.models import Model, Q, QuerySet

from apps.common.authz import Filter, Principal, Resource, ownership_filter

__all__ = ["as_q", "apply_filter", "scoped"]

_M = TypeVar("_M", bound=Model)


class UnscopableFilterError(RuntimeError):
    """A filter shape reached the translator that it cannot express.

    Fatal rather than degrading to an unfiltered queryset: the failure mode of
    "authorisation could not be applied" must never be "authorisation was not
    applied".
    """


def as_q(f: Filter) -> Q:
    """The `Q` for a filter that restricts something.

    `ALLOW_ALL` and `DENY_ALL` have no `Q` representation that composes safely
    — `Q()` matches everything and `~Q()` is not reliably empty — so they are
    rejected here and handled by `apply_filter`, which can reach for
    `.all()` and `.none()` instead.
    """
    if f.kind == "EQUALS":
        assert f.row_field is not None
        return Q(**{f.row_field: f.value})
    if f.kind == "ANY_OF":
        combined = Q()
        for branch in f.branches:
            combined |= as_q(branch)
        return combined
    raise UnscopableFilterError(f"{f.kind} has no Q representation; use apply_filter")


def apply_filter(queryset: QuerySet[_M], f: Filter) -> QuerySet[_M]:
    """Restrict a queryset to the rows a filter admits."""
    if f.kind == "ALLOW_ALL":
        return queryset
    if f.kind == "DENY_ALL":
        return queryset.none()
    return queryset.filter(as_q(f))


def scoped(
    queryset: QuerySet[_M],
    principal: Principal | None,
    resource: Resource,
    *,
    write: bool = False,
) -> QuerySet[_M]:
    """The rows this principal may reach on this resource.

    An absent principal reaches nothing. An anonymous request that gets this
    far is a bug in the view's permission classes, and the safe response to a
    bug in an authorisation check is denial.
    """
    if principal is None:
        return queryset.none()
    return apply_filter(queryset, ownership_filter(principal, resource, write=write))
