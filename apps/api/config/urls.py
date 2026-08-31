"""Root URL configuration.

Every route is mounted under /api/v1 (SRS §9.1). Module routers are added
here as each module is built; Phase 1 mounted only `common`, which owns the
health endpoint; Phase 2 adds `identity`; Phase 3 adds `catalogue`; Phase 4
adds `trip`.
"""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

api_v1_patterns = [
    path("", include("apps.common.urls")),
    path("", include("apps.identity.urls")),
    path("", include("apps.catalogue.urls")),
    path("", include("apps.trip.urls")),
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
