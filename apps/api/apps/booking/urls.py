"""booking module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). §9.4.5's route.

`trips/<uuid:public_id>/quote` sits beside `trip`'s own six routes and is
served from here, which ADR 0022 records the reason for: the URL is the
tourist's and names the thing they are pricing; the code is where the module
graph allows it to be.

The route name is namespaced `v1:booking:trip-quote`, so §37.2's authorisation
matrix enumerates it under the module that implements it. A matrix that listed
it under `trip` would be describing a file that does not exist.
"""

from __future__ import annotations

from django.urls import path

from apps.booking.views import TripQuoteView

app_name = "booking"

urlpatterns = [
    path("trips/<uuid:public_id>/quote", TripQuoteView.as_view(), name="trip-quote"),
]
