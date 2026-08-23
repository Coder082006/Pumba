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
belongs with a console-specific read surface and is not part of this commit.

---

The second half of this module is the §9.3.2 **public** catalogue: the
endpoints a tourist reaches before signing in, and the ones Google indexes.

**Unauthenticated by design, and filtered by visibility rather than by
ownership.** There is no principal to scope against, so the control is
`domain.visibility` — walked over the whole country → region → destination →
listing chain by `selectors.visible`, on every list and every detail. §4.1's
Pemba switch and a market's `launch_date` are enforced there and nowhere else,
which is why `tests/test_catalogue_public_api.py` asserts it per route and
fails the build for a public route that has no such assertion.

**A detail row is addressed by `public_id` or by slug.** §7.2 makes the UUID
the identifier the API exchanges; §24.8 serves pages from slugs, because
`/destinations/zanzibar` is what a person links. `selectors.reference_q` owns
the resolution.

**A hidden row and a missing one answer identically.** Both are 404. A
distinguishable "exists but not yet public" publishes the launch date of a
market that has not opened — the same disclosure §30.3 refuses on the
authenticated side, arrived at from the other direction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, ClassVar
from uuid import UUID

from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.catalogue import selectors, services
from apps.catalogue import serializers as ser
from apps.catalogue.domain.ranking import SortOption
from apps.catalogue.domain.search import SearchKind, SearchQueryError
from apps.common.authentication import principal_from_request
from apps.common.authz import Permission
from apps.common.config import get_setting
from apps.common.envelope import success_envelope
from apps.common.errors import NotFoundError, ValidationError
from apps.common.mixins import ScopedQuerysetMixin
from apps.common.pagination import Page
from apps.common.permissions import (
    EmailVerified,
    HasPermission,
    IsAuthenticatedPrincipal,
    MfaSatisfied,
)
from apps.common.serializers import StrictSerializer
from apps.common.throttling import CatalogueReadThrottle

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
    "AdminCancellationPolicyCreateView",
    "AdminCancellationPolicyDetailView",
    "AdminCancellationPolicyRestoreView",
    "AdminAttractionCreateView",
    "AdminAttractionDetailView",
    "AdminAttractionRestoreView",
    "AdminActivityCreateView",
    "AdminActivityDetailView",
    "AdminActivityRestoreView",
    "AdminAccommodationCreateView",
    "AdminAccommodationDetailView",
    "AdminAccommodationRestoreView",
    "DestinationListView",
    "DestinationDetailView",
    "AttractionListView",
    "AttractionDetailView",
    "ActivityListView",
    "ActivityDetailView",
    "AccommodationListView",
    "AccommodationDetailView",
    "SearchView",
    "TagListView",
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


@_create_schema("cancellation_policy")
class AdminCancellationPolicyCreateView(_AdminCreateView):
    entity_key = "cancellation_policy"


@_detail_schema("cancellation_policy")
class AdminCancellationPolicyDetailView(_AdminDetailView):
    entity_key = "cancellation_policy"


@_restore_schema("cancellation_policy")
class AdminCancellationPolicyRestoreView(_AdminRestoreView):
    entity_key = "cancellation_policy"


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


# ---------------------------------------------------------------------------
# The public catalogue — SRS §9.3.2
# ---------------------------------------------------------------------------


def _today() -> date:
    """The date the visibility rule is evaluated against.

    UTC, deliberately, and not the destination's local date. A list spans many
    destinations in many zones, and evaluating each row in its own zone would
    make one request return a set no single clock agrees with. `launch_date` is
    an editorial decision measured in days, and `Destination.today_local`
    exists for the places that genuinely need the local one.
    """
    return timezone.now().date()


def _page_size(requested: int | None) -> int:
    """§9.1's `?limit`, bounded by `system_setting` rather than by a constant.

    An unbounded limit on a public endpoint is a way to ask for the whole
    catalogue, its ancestor chain and its galleries in one query — which is
    why the ceiling exists, and why an administrator can lower it during an
    incident without a deployment (NFR-M07).
    """
    ceiling = int(get_setting("page.max_size"))
    if requested is None:
        return min(int(get_setting("page.default_size")), ceiling)
    return min(requested, ceiling)


class _PublicCatalogueView(APIView):
    """Unauthenticated, throttled, and named so the URL-conf audit sees it.

    §9.3.2 makes these public. §9.6 throttles them by IP, because they are the
    only unauthenticated endpoints that run a seven-term ordering over a joined
    query, which makes them the cheapest thing here to point a script at.
    """

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [CatalogueReadThrottle]

    #: What this view accepts in the query string. Strict, so a mistyped
    #: filter is a 422 naming it rather than a full, unfiltered 200.
    query_serializer: ClassVar[type[StrictSerializer]]

    def _query(self, request: Request) -> dict[str, Any]:
        payload = self.query_serializer(data=request.query_params)
        payload.is_valid(raise_exception=True)
        return dict(payload.validated_data)

    @staticmethod
    def _tags(query: Mapping[str, Any]) -> list[str]:
        return list(query.get("tags", ()))

    @staticmethod
    def _sort(query: Mapping[str, Any]) -> SortOption:
        return SortOption(query.get("sort", SortOption.DEFAULT.value))

    def _rendered(self, page: Page[Any], serializer: type[Serializer[Any]]) -> Response:
        """§9.2's list envelope: the rows in `data`, the cursor in `meta`."""
        return Response(
            success_envelope(
                [dict(serializer(item).data) for item in page.items],
                {"next_cursor": page.next_cursor},
            )
        )


class _PublicDetailView(_PublicCatalogueView):
    """One row, or 404 — whether it is missing or merely not public yet."""

    def _detail(self, dto: Any, serializer: type[Serializer[Any]]) -> Response:
        if dto is None:
            raise NotFoundError()
        return Response(success_envelope(dict(serializer(dto).data)))


@extend_schema(
    parameters=[ser.DestinationQuerySerializer],
    responses={200: ser.DestinationSerializer(many=True)},
    summary="List published destinations",
    tags=["catalogue"],
    auth=[],
)
class DestinationListView(_PublicCatalogueView):
    """§9.3.2. Also the source `app/sitemap.ts` enumerates (commit 34)."""

    query_serializer = ser.DestinationQuerySerializer

    def get(self, request: Request) -> Response:
        query = self._query(request)
        page = selectors.list_destinations(
            today=_today(),
            region_slug=query.get("region"),
            is_gateway=query.get("is_gateway"),
            limit=_page_size(query.get("limit")),
            cursor=query.get("cursor"),
        )
        return self._rendered(page, ser.DestinationSerializer)


@extend_schema(
    responses={200: ser.DestinationSerializer},
    summary="Read one destination",
    tags=["catalogue"],
    auth=[],
)
class DestinationDetailView(_PublicDetailView):
    query_serializer = ser.DestinationQuerySerializer

    def get(self, request: Request, reference: str) -> Response:
        return self._detail(
            selectors.get_destination(reference=reference, today=_today()),
            ser.DestinationSerializer,
        )


@extend_schema(
    parameters=[ser.AttractionQuerySerializer],
    responses={200: ser.AttractionSerializer(many=True)},
    summary="List attractions",
    tags=["catalogue"],
    auth=[],
)
class AttractionListView(_PublicCatalogueView):
    query_serializer = ser.AttractionQuerySerializer

    def get(self, request: Request) -> Response:
        query = self._query(request)
        page = selectors.list_attractions(
            today=_today(),
            destination_slug=query.get("destination"),
            tags=self._tags(query),
            sort=self._sort(query),
            limit=_page_size(query.get("limit")),
            cursor=query.get("cursor"),
        )
        return self._rendered(page, ser.AttractionSerializer)


@extend_schema(
    responses={200: ser.AttractionSerializer},
    summary="Read one attraction",
    tags=["catalogue"],
    auth=[],
)
class AttractionDetailView(_PublicDetailView):
    query_serializer = ser.AttractionQuerySerializer

    def get(self, request: Request, reference: str) -> Response:
        return self._detail(
            selectors.get_attraction(reference=reference, today=_today()),
            ser.AttractionSerializer,
        )


@extend_schema(
    parameters=[ser.ActivityQuerySerializer],
    responses={200: ser.ActivitySerializer(many=True)},
    summary="List activities",
    tags=["catalogue"],
    auth=[],
)
class ActivityListView(_PublicCatalogueView):
    query_serializer = ser.ActivityQuerySerializer

    def get(self, request: Request) -> Response:
        query = self._query(request)
        page = selectors.list_activities(
            today=_today(),
            destination_slug=query.get("destination"),
            tags=self._tags(query),
            sort=self._sort(query),
            limit=_page_size(query.get("limit")),
            cursor=query.get("cursor"),
        )
        return self._rendered(page, ser.ActivitySerializer)


@extend_schema(
    responses={200: ser.ActivitySerializer},
    summary="Read one activity",
    tags=["catalogue"],
    auth=[],
)
class ActivityDetailView(_PublicDetailView):
    query_serializer = ser.ActivityQuerySerializer

    def get(self, request: Request, reference: str) -> Response:
        return self._detail(
            selectors.get_activity(reference=reference, today=_today()),
            ser.ActivitySerializer,
        )


@extend_schema(
    parameters=[ser.AccommodationQuerySerializer],
    responses={200: ser.AccommodationSerializer(many=True)},
    summary="List curated accommodation locations",
    description=(
        "Curated location records — ADR 0013. The Platform does not sell the "
        "room in v1, so there is no availability, no rate and no date filter: "
        "this is the list §24.11 offers a tourist naming where they already "
        "intend to stay."
    ),
    tags=["catalogue"],
    auth=[],
)
class AccommodationListView(_PublicCatalogueView):
    query_serializer = ser.AccommodationQuerySerializer

    def get(self, request: Request) -> Response:
        query = self._query(request)
        page = selectors.list_accommodation(
            today=_today(),
            destination_slug=query.get("destination"),
            property_types=list(query.get("property_type", ())),
            limit=_page_size(query.get("limit")),
            cursor=query.get("cursor"),
        )
        return self._rendered(page, ser.AccommodationSerializer)


@extend_schema(
    responses={200: ser.AccommodationSerializer},
    summary="Read one accommodation location",
    tags=["catalogue"],
    auth=[],
)
class AccommodationDetailView(_PublicDetailView):
    query_serializer = ser.AccommodationQuerySerializer

    def get(self, request: Request, reference: str) -> Response:
        return self._detail(
            selectors.get_accommodation(reference=reference, today=_today()),
            ser.AccommodationSerializer,
        )


@extend_schema(
    parameters=[ser.SearchQuerySerializer],
    responses={200: ser.SearchHitSerializer(many=True)},
    summary="Search the catalogue",
    description=(
        "Full-text search across destinations, attractions, activities and "
        "accommodation, merged into one relevance-ordered list. A bounded "
        "top-N rather than a paginated walk: the way to see more of one kind "
        "is that kind's listing endpoint."
    ),
    tags=["catalogue"],
    auth=[],
)
class SearchView(_PublicCatalogueView):
    """§9.3.2's `GET /search`. §24.7's box.

    The one endpoint on the platform that puts arbitrary public text into a
    database query, which is why `domain.search` stands between the two.
    `websearch_to_tsquery` accepts what a person types — quotes, `or`, a stray
    ampersand — where `to_tsquery` raises a database error on any of them, and
    a 500 on `GET /search?q=fish %26 chips` is both a bug and a cheap denial of
    service on an unauthenticated URL.
    """

    query_serializer = ser.SearchQuerySerializer

    def get(self, request: Request) -> Response:
        query = self._query(request)
        try:
            hits = selectors.search(
                query["q"],
                today=_today(),
                min_length=int(get_setting("search.min_length")),
                max_length=int(get_setting("search.max_length")),
                kinds=[SearchKind(kind) for kind in query.get("kind", ())],
                limit_per_kind=int(get_setting("search.results_per_kind")),
            )
        except SearchQueryError as exc:
            # §24.7's "requires two characters", surfacing as 422 rather than
            # as a scan of every row in the catalogue. Translated here because
            # `domain/` may not import the platform's error hierarchy — the
            # interface layer is where a domain refusal becomes a status code.
            raise ValidationError(str(exc), details=[{"field": "q", "issue": str(exc)}]) from exc

        return Response(success_envelope([dict(ser.SearchHitSerializer(hit).data) for hit in hits]))


@extend_schema(
    responses={200: ser.TagSerializer(many=True)},
    summary="List the interest vocabulary",
    description=(
        "The §24.7 category chips. Rows, not code: adding an interest is an "
        "administrator action and reaches the chip strip with no deployment, "
        "and retiring one removes it the same way."
    ),
    tags=["catalogue"],
    auth=[],
)
class TagListView(_PublicCatalogueView):
    """§24.7's chip vocabulary.

    Unpaginated, and that is a decision rather than an omission. This is a
    curated closed vocabulary an administrator writes by hand — §24.7 names
    five of them — and the client renders it as one strip. A cursor here would
    make a front end loop to draw a row of chips, and would make the response
    uncacheable as a whole for no benefit. It is also the one catalogue list
    with no visibility chain, because a tag has no parent: retired and
    deactivated are the whole of its lifecycle.
    """

    query_serializer = ser.NoQuerySerializer

    def get(self, request: Request) -> Response:
        self._query(request)
        return Response(
            success_envelope([dict(ser.TagSerializer(tag).data) for tag in selectors.list_tags()])
        )
