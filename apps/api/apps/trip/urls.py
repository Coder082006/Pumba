"""trip module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). §9.4.2's routes.

Route names are namespaced (`v1:trip:trip-detail`) because §37.2's
authorisation matrix enumerates endpoints by name — a renamed route should
surface as a matrix change rather than as an endpoint that quietly stopped
being checked. Plural collections, singular details, no trailing slashes: the
shape `identity` and `catalogue` already use.

`<uuid:public_id>` rather than `<str:reference>`, unlike the public catalogue
routes. A destination is addressable by slug because §24.8 wants
`/destinations/stone-town` to be a shareable URL; a trip is nobody's shareable
URL, its reference is a support-desk identifier rather than a locator, and
accepting two forms would publish two ways to address a private resource for
no gain.

`generate` and `cancel` are sub-resources rather than a status field on the
PATCH. Both are operations with side effects beyond the row — one rewrites the
itinerary and archives a version, the other moves a state machine — and §41.13
records them as distinct actions. A PATCH that accepted `{"status":
"CANCELLED"}` would also make `status` look writable, which
`repositories.NEVER_WRITABLE` is explicit that it is not.
"""

from __future__ import annotations

from django.urls import path

from apps.trip.views import (
    TripCancelView,
    TripDetailView,
    TripFlightsView,
    TripGenerateView,
    TripItemDetailView,
    TripItemsView,
    TripListCreateView,
)

app_name = "trip"

urlpatterns = [
    path("trips", TripListCreateView.as_view(), name="trip-list"),
    path("trips/<uuid:public_id>", TripDetailView.as_view(), name="trip-detail"),
    path("trips/<uuid:public_id>/items", TripItemsView.as_view(), name="trip-items"),
    path(
        "trips/<uuid:public_id>/items/<uuid:item_id>",
        TripItemDetailView.as_view(),
        name="trip-item-detail",
    ),
    path("trips/<uuid:public_id>/flights", TripFlightsView.as_view(), name="trip-flights"),
    path(
        "trips/<uuid:public_id>/itinerary/generate",
        TripGenerateView.as_view(),
        name="trip-generate",
    ),
    path("trips/<uuid:public_id>/cancel", TripCancelView.as_view(), name="trip-cancel"),
]
