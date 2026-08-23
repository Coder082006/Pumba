"""catalogue module — SRS §6.4.

Interface layer (SRS §8.2 layer 1).

The §27.8 console's routes. Everything here is under `admin/` because
everything here requires `CATALOGUE_MANAGE`; the public catalogue endpoints of
§9.3.2 are unauthenticated and land beside these, not inside them.

Route names are stable and namespaced (`v1:catalogue:admin-destination-detail`)
because the §37.2 authorisation matrix enumerates them by name — a renamed
route should surface as a matrix change rather than as an endpoint that quietly
stopped being checked.

Plural collection paths, singular detail paths, and no trailing slashes, which
is the shape §9.1 already established for `identity`. `restore` is a sub-
resource of the row rather than a query parameter on the PATCH: undoing a
deletion is a different operation from amending a row, and §41.13 records them
under different actions.

The public §9.3.2 routes sit beside them, not beneath them. `<str:reference>`
rather than `<uuid:public_id>` because a public detail row is addressable by
either its identifier or its slug (§24.8) — two routes for one resource would
publish two operations in the OpenAPI document for the same thing, and
`selectors.reference_q` resolves which is which in one place.
"""

from django.urls import path

from apps.catalogue.views import (
    AccommodationDetailView,
    AccommodationListView,
    ActivityDetailView,
    ActivityListView,
    AdminAccommodationCreateView,
    AdminAccommodationDetailView,
    AdminAccommodationRestoreView,
    AdminActivityCreateView,
    AdminActivityDetailView,
    AdminActivityRestoreView,
    AdminAttractionCreateView,
    AdminAttractionDetailView,
    AdminAttractionRestoreView,
    AdminCancellationPolicyCreateView,
    AdminCancellationPolicyDetailView,
    AdminCancellationPolicyRestoreView,
    AdminCountryCreateView,
    AdminCountryDetailView,
    AdminCountryRestoreView,
    AdminDestinationCreateView,
    AdminDestinationDetailView,
    AdminDestinationRestoreView,
    AdminRegionCreateView,
    AdminRegionDetailView,
    AdminRegionRestoreView,
    AdminTagCreateView,
    AdminTagDetailView,
    AdminTagRestoreView,
    AttractionDetailView,
    AttractionListView,
    DestinationDetailView,
    DestinationListView,
    SearchView,
    TagListView,
)

app_name = "catalogue"

urlpatterns = [
    path("admin/countries", AdminCountryCreateView.as_view(), name="admin-country-create"),
    path(
        "admin/countries/<uuid:public_id>",
        AdminCountryDetailView.as_view(),
        name="admin-country-detail",
    ),
    path(
        "admin/countries/<uuid:public_id>/restore",
        AdminCountryRestoreView.as_view(),
        name="admin-country-restore",
    ),
    path("admin/regions", AdminRegionCreateView.as_view(), name="admin-region-create"),
    path(
        "admin/regions/<uuid:public_id>",
        AdminRegionDetailView.as_view(),
        name="admin-region-detail",
    ),
    path(
        "admin/regions/<uuid:public_id>/restore",
        AdminRegionRestoreView.as_view(),
        name="admin-region-restore",
    ),
    path(
        "admin/destinations", AdminDestinationCreateView.as_view(), name="admin-destination-create"
    ),
    path(
        "admin/destinations/<uuid:public_id>",
        AdminDestinationDetailView.as_view(),
        name="admin-destination-detail",
    ),
    path(
        "admin/destinations/<uuid:public_id>/restore",
        AdminDestinationRestoreView.as_view(),
        name="admin-destination-restore",
    ),
    path("admin/tags", AdminTagCreateView.as_view(), name="admin-tag-create"),
    path("admin/tags/<uuid:public_id>", AdminTagDetailView.as_view(), name="admin-tag-detail"),
    path(
        "admin/tags/<uuid:public_id>/restore",
        AdminTagRestoreView.as_view(),
        name="admin-tag-restore",
    ),
    path(
        "admin/cancellation-policies",
        AdminCancellationPolicyCreateView.as_view(),
        name="admin-cancellation-policy-create",
    ),
    path(
        "admin/cancellation-policies/<uuid:public_id>",
        AdminCancellationPolicyDetailView.as_view(),
        name="admin-cancellation-policy-detail",
    ),
    path(
        "admin/cancellation-policies/<uuid:public_id>/restore",
        AdminCancellationPolicyRestoreView.as_view(),
        name="admin-cancellation-policy-restore",
    ),
    path("admin/attractions", AdminAttractionCreateView.as_view(), name="admin-attraction-create"),
    path(
        "admin/attractions/<uuid:public_id>",
        AdminAttractionDetailView.as_view(),
        name="admin-attraction-detail",
    ),
    path(
        "admin/attractions/<uuid:public_id>/restore",
        AdminAttractionRestoreView.as_view(),
        name="admin-attraction-restore",
    ),
    path("admin/activities", AdminActivityCreateView.as_view(), name="admin-activity-create"),
    path(
        "admin/activities/<uuid:public_id>",
        AdminActivityDetailView.as_view(),
        name="admin-activity-detail",
    ),
    path(
        "admin/activities/<uuid:public_id>/restore",
        AdminActivityRestoreView.as_view(),
        name="admin-activity-restore",
    ),
    path(
        "admin/accommodation",
        AdminAccommodationCreateView.as_view(),
        name="admin-accommodation-create",
    ),
    path(
        "admin/accommodation/<uuid:public_id>",
        AdminAccommodationDetailView.as_view(),
        name="admin-accommodation-detail",
    ),
    path(
        "admin/accommodation/<uuid:public_id>/restore",
        AdminAccommodationRestoreView.as_view(),
        name="admin-accommodation-restore",
    ),
    # --- the public catalogue, SRS §9.3.2 --------------------------------
    path("destinations", DestinationListView.as_view(), name="destination-list"),
    path(
        "destinations/<str:reference>",
        DestinationDetailView.as_view(),
        name="destination-detail",
    ),
    path("attractions", AttractionListView.as_view(), name="attraction-list"),
    path("attractions/<str:reference>", AttractionDetailView.as_view(), name="attraction-detail"),
    path("activities", ActivityListView.as_view(), name="activity-list"),
    path("activities/<str:reference>", ActivityDetailView.as_view(), name="activity-detail"),
    path("accommodation", AccommodationListView.as_view(), name="accommodation-list"),
    path(
        "accommodation/<str:reference>",
        AccommodationDetailView.as_view(),
        name="accommodation-detail",
    ),
    path("search", SearchView.as_view(), name="search"),
    path("tags", TagListView.as_view(), name="tag-list"),
]
