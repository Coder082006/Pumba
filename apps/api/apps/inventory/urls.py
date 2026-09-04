"""inventory module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). Two routes: §9.3.2's public departures
list, and §26.5's calendar under `admin/`, mirroring where `catalogue.urls`
puts the §27.8 console.

**The path says `activities` and the code lives here**, which is ADR 0011's
third consequence rather than an inconsistency: `catalogue` owns the activity
and may not compose inventory data, so the endpoint that joins the two is
served from the module that is allowed to see both. The URL is the tourist's,
and it names the thing they are looking at.

`<str:reference>` matches `catalogue.urls`: §7.2 makes the UUID the identifier
the API exchanges and §24.8 serves pages from slugs, and `resolve_listing_ref`
accepts either. A departures list reached by a different kind of identifier
from the activity page above it would be a needless second convention.
"""

from __future__ import annotations

from django.urls import path

from apps.inventory.views import ActivityDeparturesView, AdminActivityDeparturesView

app_name = "inventory"

urlpatterns = [
    path(
        "activities/<str:reference>/departures",
        ActivityDeparturesView.as_view(),
        name="activity-departures",
    ),
    # `<uuid:public_id>` rather than the public route's `<str:reference>`. A
    # console addresses a row by its identifier: a slug is what the listing is
    # called and is free to change, and a bulk capacity edit is the last place
    # a stale bookmark should resolve to a different activity.
    path(
        "admin/activities/<uuid:public_id>/departures",
        AdminActivityDeparturesView.as_view(),
        name="admin-activity-departures",
    ),
]
