"""catalogue module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). No business logic, no ORM queries.

The §27.8 administration surface: *"the catalogue console creates a country, a
region and a destination with no code change and no deployment"*. That sentence
is what §41.12 tests and what these views exist to make true.

**Both checks of §30.3, in the right order and in the right places.** The role
check is declarative — `HasPermission.for_(Permission.CATALOGUE_MANAGE)`, plus
the §30.2 obligations that come with an administrative role. The ownership
check is a queryset filter through `ScopedQuerysetMixin`, never
`has_object_permission`, so a row a principal may not reach raises `Http404`
inside `get_object()` and there is no branch that could answer 403 instead.

**Every view is thin by construction.** It validates a shape, calls one service
function, and renders a DTO. The two-line bodies are not an accident of a small
feature: a view that did anything else would be a second place the §41.13 audit
entry could be forgotten, and the design is that there is no such place.

**Seven entities, three shapes.** `_AdminCreateView`, `_AdminDetailView` and
`_AdminRestoreView` carry the behaviour; the twenty-one classes below carry a
name, an `entity_key` and a schema. The names are spelled out rather than
generated because they are the route names the §37.2 authorisation matrix
enumerates and the operations in the published OpenAPI document (§36.2) — both
of which should be greppable strings in this file rather than something a
reader has to execute the module to discover.

**There is no admin read endpoint here.** A create or an amendment returns the
row it wrote, which is what a console form needs back; listing and fetching
curated rows — including the soft-deleted ones an administrator restores from —
belong with the read API and its pagination, and shipping half of that here
would mean shipping a gallery-less detail payload that later has to change
shape.
"""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response

from apps.catalogue import serializers as ser
from apps.catalogue import services
from apps.common.authentication import principal_from_request
from apps.common.authz import Permission
from apps.common.envelope import success_envelope
from apps.common.mixins import ScopedQuerysetMixin
from apps.common.permissions import (
    EmailVerified,
    HasPermission,
    IsAuthenticatedPrincipal,
    MfaSatisfied,
)

__all__ = [
    "ADMIN_PERMISSIONS",
    "AdminCountryCreateView",
    "AdminCountryDetailView",
    "AdminCountryRestoreView",
    "AdminRegionCreateView",
    "AdminRegionDetailView",
    "AdminRegionRestoreView",
    "AdminDestinationCreateView",
    "AdminDestinationDetailView",
    "AdminDestinationRestoreView",
    "AdminTagCreateView",
    "AdminTagDetailView",
    "AdminTagRestoreView",
    "AdminAttractionCreateView",
    "AdminAttractionDetailView",
    "AdminAttractionRestoreView",
    "AdminActivityCreateView",
    "AdminActivityDetailView",
    "AdminActivityRestoreView",
    "AdminAccommodationCreateView",
    "AdminAccommodationDetailView",
    "AdminAccommodationRestoreView",
]

#: §5.2 grants `CATALOGUE_MANAGE` to CATALOGUE_ADMIN and, by composition, to
#: SUPER_ADMIN — and to nobody else: *"cannot alter payments or catalogue"* is
#: the sentence that keeps SUPPORT_AGENT off this surface. §30.2 adds the two
#: obligations that come with holding an administrative role at all: a verified
#: address, and TOTP satisfied *on this session* rather than merely enrolled.
ADMIN_PERMISSIONS: list[type[BasePermission]] = [
    IsAuthenticatedPrincipal,
    HasPermission.for_(Permission.CATALOGUE_MANAGE),
    EmailVerified,
    MfaSatisfied,
]


def _client_ip(request: Request) -> str | None:
    """The peer address, from the socket, not from a header.

    `X-Forwarded-For` is client-controlled, and §41.13 puts the IP in the audit
    entry — an address the caller chooses is an address that proves nothing
    about who made the change.
    """
    return request.META.get("REMOTE_ADDR")


class _AdminView:
    """What the three shapes share: which entity, and who may reach it.

    `ownership_resource` is derived from the entity rather than declared on the
    view. Declaring it twice would let a view be pointed at `accommodation`
    while scoping by `attraction`, and nothing downstream would notice: both
    resources resolve to the same rule for the same roles today, so the mistake
    would stay invisible until the day §5.2 gave them different ones.
    """

    entity_key: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        key = cls.__dict__.get("entity_key")
        if key is None:
            return
        entity = services.entity_for(key)
        cls.queryset = entity.model.all_objects.all()  # type: ignore[attr-defined]
        if issubclass(cls, ScopedQuerysetMixin):
            # Only where a filter actually runs. A resource declared on a view
            # that looks up no row would report a control to the §37.2 matrix
            # that the endpoint does not apply.
            cls.ownership_resource = entity.resource

    @property
    def entity(self) -> services.CatalogueEntity:
        return services.entity_for(self.entity_key)

    def _render(self, dto: Any) -> dict[str, Any]:
        return dict(ser.READ_SERIALIZERS[self.entity_key](dto).data)

    def _validated(self, request: Request, *, partial: bool) -> dict[str, Any]:
        payload = ser.WRITE_SERIALIZERS[self.entity_key](data=request.data, partial=partial)
        payload.is_valid(raise_exception=True)
        return dict(payload.validated_data)


class _AdminCreateView(_AdminView, generics.GenericAPIView):  # type: ignore[type-arg]
    """POST one new curated row.

    Deliberately not a `ScopedQuerysetMixin` view. There is no row to scope
    yet, so a filter here would provably run against nothing while reporting to
    the §37.2 matrix that ownership was enforced. What stands between a caller
    and a new row is the role check, and the matrix records that by name in
    `NO_ROWS_EXPOSED` rather than by an inherited class that does nothing.
    """

    permission_classes = ADMIN_PERMISSIONS

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        dto = services.create(
            self.entity,
            fields=self._validated(request, partial=False),
            principal=principal_from_request(request),
            ip=_client_ip(request),
        )
        return Response(success_envelope(self._render(dto)), status=status.HTTP_201_CREATED)


class _AdminDetailView(_AdminView, ScopedQuerysetMixin, generics.GenericAPIView):  # type: ignore[type-arg]
    """Amend or retire one curated row.

    `get_object()` runs against the scoped queryset, so a principal who may not
    reach this row is told it does not exist — §30.3, structurally rather than
    by a branch. It is called before every mutation for exactly that reason,
    even though the service works by `public_id` and does not need the row
    itself.

    The queryset is `all_objects`: an administrator amends rows that are not
    public — that is most of what §27.8 is for — and `selectors.visible` is the
    public path, not this one.
    """

    permission_classes = ADMIN_PERMISSIONS
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def patch(self, request: Request, public_id: UUID) -> Response:
        """A partial update — including the one that opens or closes a market.

        §4.1 wants a destination published and withdrawn without a deployment,
        and both are `is_active` moving in this one call. There is deliberately
        no separate activate endpoint, so there is no second path that could
        record the change differently or not at all.
        """
        self.get_object()
        dto = services.update(
            self.entity,
            public_id,
            fields=self._validated(request, partial=True),
            principal=principal_from_request(request),
            ip=_client_ip(request),
        )
        return Response(success_envelope(self._render(dto)))

    def delete(self, request: Request, public_id: UUID) -> Response:
        """§7.7's soft deletion. The row survives; the slug is released."""
        self.get_object()
        services.delete(
            self.entity,
            public_id,
            principal=principal_from_request(request),
            ip=_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class _AdminRestoreView(_AdminView, ScopedQuerysetMixin, generics.GenericAPIView):  # type: ignore[type-arg]
    """Undo a soft deletion.

    A POST to its own resource rather than a PATCH setting `deleted_at`.
    `deleted_at` is not a writable field, and adding it to the update
    serializer to support this would open the mass-assignment hole that
    `repositories._WRITABLE` exists to close.
    """

    permission_classes = ADMIN_PERMISSIONS
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def post(self, request: Request, public_id: UUID) -> Response:
        self.get_object()
        services.restore(
            self.entity,
            public_id,
            principal=principal_from_request(request),
            ip=_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Schema helpers — §36.2 commits the generated document, so these shapes are
# the published contract rather than a description of it.
# ---------------------------------------------------------------------------

_TAGS = ["catalogue-admin"]


def _noun(entity_key: str) -> str:
    return entity_key.replace("_", " ")


def _create_schema(entity_key: str) -> Any:
    return extend_schema_view(
        post=extend_schema(
            request=ser.WRITE_SERIALIZERS[entity_key],
            responses={201: ser.READ_SERIALIZERS[entity_key]},
            summary=f"Create a {_noun(entity_key)}",
            tags=_TAGS,
        )
    )


def _detail_schema(entity_key: str) -> Any:
    return extend_schema_view(
        patch=extend_schema(
            request=ser.WRITE_SERIALIZERS[entity_key],
            responses={200: ser.READ_SERIALIZERS[entity_key]},
            summary=f"Amend a {_noun(entity_key)}",
            tags=_TAGS,
        ),
        delete=extend_schema(
            responses={204: None},
            summary=f"Retire a {_noun(entity_key)}",
            tags=_TAGS,
        ),
    )


def _restore_schema(entity_key: str) -> Any:
    return extend_schema_view(
        post=extend_schema(
            request=None,
            responses={204: None},
            summary=f"Restore a retired {_noun(entity_key)}",
            tags=_TAGS,
        )
    )


# ---------------------------------------------------------------------------
# The twenty-one endpoints. Three per entity: create, detail, restore.
# ---------------------------------------------------------------------------


@_create_schema("country")
class AdminCountryCreateView(_AdminCreateView):
    entity_key = "country"


@_detail_schema("country")
class AdminCountryDetailView(_AdminDetailView):
    entity_key = "country"


@_restore_schema("country")
class AdminCountryRestoreView(_AdminRestoreView):
    entity_key = "country"


@_create_schema("region")
class AdminRegionCreateView(_AdminCreateView):
    entity_key = "region"


@_detail_schema("region")
class AdminRegionDetailView(_AdminDetailView):
    entity_key = "region"


@_restore_schema("region")
class AdminRegionRestoreView(_AdminRestoreView):
    entity_key = "region"


@_create_schema("destination")
class AdminDestinationCreateView(_AdminCreateView):
    entity_key = "destination"


@_detail_schema("destination")
class AdminDestinationDetailView(_AdminDetailView):
    entity_key = "destination"


@_restore_schema("destination")
class AdminDestinationRestoreView(_AdminRestoreView):
    entity_key = "destination"


@_create_schema("tag")
class AdminTagCreateView(_AdminCreateView):
    entity_key = "tag"


@_detail_schema("tag")
class AdminTagDetailView(_AdminDetailView):
    entity_key = "tag"


@_restore_schema("tag")
class AdminTagRestoreView(_AdminRestoreView):
    entity_key = "tag"


@_create_schema("attraction")
class AdminAttractionCreateView(_AdminCreateView):
    entity_key = "attraction"


@_detail_schema("attraction")
class AdminAttractionDetailView(_AdminDetailView):
    entity_key = "attraction"


@_restore_schema("attraction")
class AdminAttractionRestoreView(_AdminRestoreView):
    entity_key = "attraction"


@_create_schema("activity")
class AdminActivityCreateView(_AdminCreateView):
    entity_key = "activity"


@_detail_schema("activity")
class AdminActivityDetailView(_AdminDetailView):
    entity_key = "activity"


@_restore_schema("activity")
class AdminActivityRestoreView(_AdminRestoreView):
    entity_key = "activity"


@_create_schema("accommodation")
class AdminAccommodationCreateView(_AdminCreateView):
    entity_key = "accommodation"


@_detail_schema("accommodation")
class AdminAccommodationDetailView(_AdminDetailView):
    entity_key = "accommodation"


@_restore_schema("accommodation")
class AdminAccommodationRestoreView(_AdminRestoreView):
    entity_key = "accommodation"
