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
    AdminActivityProviderView,
    AdminBookingForceTransitionView,
    AdminBookingVoucherReissueView,
    AdminCommissionRuleDetailView,
    AdminCommissionRuleListCreateView,
    AdminCorridorCreateView,
    AdminCorridorDetailView,
    AdminFinanceReportView,
    AdminPaymentListView,
    AdminPayoutApproveView,
    AdminPayoutListView,
    AdminPayoutReleaseView,
    AdminProviderDetailView,
    AdminProviderEarningsView,
    AdminProviderListView,
    AdminProviderStatusView,
    AdminQuotePreviewView,
    AdminRefundListCreateView,
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
    # §27.7's provider console, ADR 0025.
    path("admin/providers", AdminProviderListView.as_view(), name="admin-provider-list"),
    path(
        "admin/providers/<uuid:public_id>",
        AdminProviderDetailView.as_view(),
        name="admin-provider-detail",
    ),
    path(
        "admin/providers/<uuid:public_id>/status",
        AdminProviderStatusView.as_view(),
        name="admin-provider-status",
    ),
    path(
        "admin/activities/<uuid:public_id>/provider",
        AdminActivityProviderView.as_view(),
        name="admin-activity-provider",
    ),
    # §9.3 / §27.9: exceptional booking controls. BR-038.
    path(
        "admin/bookings/<uuid:public_id>/force-transition",
        AdminBookingForceTransitionView.as_view(),
        name="admin-booking-force-transition",
    ),
    # §27.10's payment and refund console, and §9.3.7's `POST /refunds`.
    # Here rather than in `payment` because the console reads across modules
    # and `administration` is the one that may — the same argument that puts
    # the tariff and provider consoles here.
    path("admin/payments", AdminPaymentListView.as_view(), name="admin-payment-list"),
    # §22.2's commercial rules and §22.5's payouts. Here rather than in
    # `finance` for the reason the other consoles are: a rule names a provider
    # and a listing, and `administration` is the module that may resolve both.
    path(
        "admin/commission-rules",
        AdminCommissionRuleListCreateView.as_view(),
        name="admin-commission-rule-list",
    ),
    path(
        "admin/commission-rules/<uuid:public_id>",
        AdminCommissionRuleDetailView.as_view(),
        name="admin-commission-rule-detail",
    ),
    path("admin/payouts", AdminPayoutListView.as_view(), name="admin-payout-list"),
    path(
        "admin/payouts/<uuid:public_id>/approve",
        AdminPayoutApproveView.as_view(),
        name="admin-payout-approve",
    ),
    path(
        "admin/payouts/<uuid:public_id>/release",
        AdminPayoutReleaseView.as_view(),
        name="admin-payout-release",
    ),
    path(
        "admin/providers/<uuid:public_id>/earnings",
        AdminProviderEarningsView.as_view(),
        name="admin-provider-earnings",
    ),
    path("admin/finance/report", AdminFinanceReportView.as_view(), name="admin-finance-report"),
    path("refunds", AdminRefundListCreateView.as_view(), name="refund-list-create"),
    path(
        "admin/bookings/<uuid:public_id>/voucher",
        AdminBookingVoucherReissueView.as_view(),
        name="admin-booking-voucher-reissue",
    ),
]
