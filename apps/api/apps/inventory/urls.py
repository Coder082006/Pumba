"""inventory module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). §9.3.2's one inventory route.

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

from apps.inventory.views import ActivityDeparturesView

app_name = "inventory"

urlpatterns = [
    path(
        "activities/<str:reference>/departures",
        ActivityDeparturesView.as_view(),
        name="activity-departures",
    ),
]
