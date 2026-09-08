"""Root URL configuration.

Every route is mounted under /api/v1 (SRS §9.1). Module routers are added
here as each module is built; Phase 1 mounted only `common`, which owns the
health endpoint; Phase 2 adds `identity`; Phase 3 adds `catalogue`; Phase 4
adds `trip`; Phase 5 adds `inventory`
and `booking`; Phase 6 adds `transport`.

`inventory` mounts `activities/{reference}/departures` — a path under
`catalogue`'s noun, served by the module that owns the counters. ADR 0011:
`catalogue` may not compose inventory data, so the endpoint that joins an
activity to its capacity lives with the capacity. `booking` mounts
`trips/{id}/quote` for the same kind of reason and a different one: §6.4
forbids `trip -> inventory`, and quoting locks inventory counters, so the use
case belongs to the only module that may see both (ADR 0022).

`transport` mounts one of §9.3.4's three routes and `trip` mounts the other
two, for the mirror-image reason: §12.4's ladder branches on the region, the
country and `is_gateway`, all of them `catalogue` facts, and §6.4 gives
`transport -> location, provider`. So the module that owns the tariff tables
serves the endpoint that names no catalogue row, and `trip` — the only module
that may see both — serves the corridor list and the quote (ADR 0023). The URLs
give no sign of the split, which is the point: a client reads §9.3.4 and finds
three endpoints under `transport/`.
"""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

api_v1_patterns = [
    path("", include("apps.common.urls")),
    path("", include("apps.identity.urls")),
    path("", include("apps.catalogue.urls")),
    path("", include("apps.trip.urls")),
    path("", include("apps.transport.urls")),
    path("", include("apps.inventory.urls")),
    path("", include("apps.booking.urls")),
    # §27.11's tariff console. `administration` has "all (read via interfaces)"
    # and is the only module that may resolve a `catalogue` name into the id a
    # `transport` table stores (ADR 0023).
    path("", include("apps.administration.urls")),
]

urlpatterns = [
    path("api/v1/", include((api_v1_patterns, "v1"), namespace="v1")),
    # Schema is generated from code and committed to packages/contracts (SRS §36.2).
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
