"""transport module — SRS §6.4.

Interface layer (SRS §8.2 layer 1).

One route. §9.3.4 lists three under `transport/`, and the other two —
`GET /transport/corridors` and `POST /transport/quotes` — are mounted by
`trip`, because both name `catalogue` rows and §6.4 gives
`transport -> location, provider` (ADR 0023).

Route names are stable and namespaced (`v1:transport:vehicle-class-list`)
because the §37.2 authorisation matrix enumerates them by name — a renamed
route should surface as a matrix change rather than as an endpoint that quietly
stopped being checked.
"""

from django.urls import path

from apps.transport.views import VehicleClassListView

app_name = "transport"

urlpatterns = [
    path(
        "transport/vehicle-classes",
        VehicleClassListView.as_view(),
        name="vehicle-class-list",
    ),
]
