"""payment module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). API-07's tourist routes.

Route names are namespaced (`v1:payment:payment-intent`) because §37.2's
authorisation matrix enumerates them by name — a renamed route should surface
as a matrix change rather than as an endpoint that quietly stopped being
checked.
"""

from django.urls import path

from apps.payment.views import PaymentDetailView, PaymentIntentView, PaymentMethodsView

app_name = "payment"

urlpatterns = [
    path("payments/intents", PaymentIntentView.as_view(), name="payment-intent"),
    path("payments/methods", PaymentMethodsView.as_view(), name="payment-methods"),
    path("payments/<uuid:public_id>", PaymentDetailView.as_view(), name="payment-detail"),
]
