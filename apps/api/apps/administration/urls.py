"""administration module — SRS §6.4.

Interface layer (SRS §8.2 layer 1).

§27.11's tariff console. §9.3.10 names `/admin/tariffs` and nothing else, so
the corridor and preview paths are added under the same `admin/transport/`
prefix rather than scattered — a console that had to remember three unrelated
roots would be a console somebody wired to two of them.

Served here rather than by `transport` because every one of these resolves a
`catalogue` row — a corridor names two destinations, a tariff names a region or
a country — and §6.4 gives `transport -> location, provider`. ADR 0023; the
same argument that puts the tourist's quote in `trip`.

Route names are stable and namespaced (`v1:administration:admin-corridor-create`)
because the §37.2 authorisation matrix enumerates them by name — a renamed route
should surface as a matrix change rather than as an endpoint that quietly
stopped being checked.
"""

from django.urls import path

from apps.administration.views import (
    AdminCorridorCreateView,
    AdminCorridorDetailView,
    AdminQuotePreviewView,
    AdminTariffCreateView,
    AdminTariffDetailView,
)

app_name = "administration"

urlpatterns = [
    path(
        "admin/transport/corridors",
        AdminCorridorCreateView.as_view(),
        name="admin-corridor-create",
    ),
    path(
        "admin/transport/corridors/<uuid:public_id>",
        AdminCorridorDetailView.as_view(),
        name="admin-corridor-detail",
    ),
    path("admin/transport/tariffs", AdminTariffCreateView.as_view(), name="admin-tariff-create"),
    path(
        "admin/transport/tariffs/<uuid:public_id>",
        AdminTariffDetailView.as_view(),
        name="admin-tariff-detail",
    ),
    path(
        "admin/transport/quote-preview",
        AdminQuotePreviewView.as_view(),
        name="admin-quote-preview",
    ),
]
