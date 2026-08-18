"""Root URL configuration.

Every route is mounted under /api/v1 (SRS §9.1). Module routers are added
here as each module is built; Phase 1 mounts only `common`, which owns the
health endpoint.
"""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

api_v1_patterns = [
    path("", include("apps.common.urls")),
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
