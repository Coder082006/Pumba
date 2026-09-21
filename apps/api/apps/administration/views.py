"""Interface layer (SRS §8.2 layer 1).

§27.11's tariff console. Every view does three things and no more: validate a
shape, call one service function, render what comes back.

**§27.1's obligations apply to all of them.** The administration console is
"reachable only from allow-listed networks, requiring mandatory TOTP, with
every action written to `audit_log`". The first two are deployment and
authentication concerns and are enforced where they belong; the third is
`services`', inside the transaction that made the change.

**None of these is a `ScopedQuerysetMixin` view, and that is recorded rather
than assumed.** §12.4 makes tariffs administrator-owned — "not provider-quoted
per booking" — and §26.4 states the provider half: "transfer pricing is
platform-managed and is displayed read-only". So every role that can reach
these holds `Scope.GLOBAL` over them, a filter here would match every row while
reporting a control to the §37.2 matrix, and the matrix records them in
`GLOBAL_BY_ROLE` where the claim is re-derived from `OWNERSHIP` on every run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.administration import serializers as ser
from apps.administration import services
from apps.common.authentication import principal_from_request
from apps.common.authz import Permission, Role
from apps.common.envelope import success_envelope
from apps.common.errors import ValidationError
from apps.common.permissions import (
    CATALOGUE_ADMIN_PERMISSIONS,
    PROVIDER_ADMIN_PERMISSIONS,
    EmailVerified,
    HasPermission,
    HasRole,
    IsAuthenticatedPrincipal,
    MfaSatisfied,
)

__all__ = [
    "AdminCorridorCreateView",
    "AdminCorridorDetailView",
    "AdminTariffCreateView",
    "AdminTariffDetailView",
    "AdminQuotePreviewView",
    "AdminProviderListView",
    "AdminProviderDetailView",
    "AdminProviderStatusView",
    "AdminActivityProviderView",
    "AdminBookingForceTransitionView",
    "AdminBookingVoucherReissueView",
]

_TAGS = ["Administration"]


class _AdminView(APIView):
    """§5.2: `CATALOGUE_ADMIN` manages "countries, regions, destinations,
    attractions, activities, **tariffs**, policies", globally."""

    permission_classes = CATALOGUE_ADMIN_PERMISSIONS

    def _ip(self, request: Request) -> str | None:
        """The peer address from the socket, never from a header.

        `X-Forwarded-For` is client-controlled, and trusting it would let an
        administrator forge the address in their own §41.13 entry — which is
        the one audit field nobody would think to doubt.
        """
        return request.META.get("REMOTE_ADDR")

    def _validated(self, request: Request, serializer: Any, *, partial: bool) -> dict[str, Any]:
        payload = serializer(data=request.data, partial=partial)
        payload.is_valid(raise_exception=True)
        return dict(payload.validated_data)


class AdminCorridorCreateView(_AdminView):
    @extend_schema(
        request=ser.CorridorWriteSerializer,
        responses={201: ser.CorridorReadSerializer},
        summary="Create a transfer corridor",
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        dto = services.create_corridor(
            fields=self._validated(request, ser.CorridorWriteSerializer, partial=False),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(
            success_envelope(dict(ser.CorridorReadSerializer(dto).data)),
            status=status.HTTP_201_CREATED,
        )


class AdminCorridorDetailView(_AdminView):
    @extend_schema(
        request=ser.CorridorWriteSerializer,
        responses={200: ser.CorridorReadSerializer},
        summary="Amend a transfer corridor",
        tags=_TAGS,
    )
    def patch(self, request: Request, public_id: UUID) -> Response:
        """A partial update — including the one that retires a corridor.

        §27.11 has no separate deactivate action and this needs none:
        withdrawing a price is `is_active` moving in this one call, which means
        there is no second path that could record the change differently or not
        at all.
        """
        dto = services.update_corridor(
            public_id,
            fields=self._validated(request, ser.CorridorWriteSerializer, partial=True),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(success_envelope(dict(ser.CorridorReadSerializer(dto).data)))


class AdminTariffCreateView(_AdminView):
    @extend_schema(
        request=ser.TariffWriteSerializer,
        responses={201: ser.TariffReadSerializer},
        summary="Create a metered transfer tariff",
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        dto = services.create_tariff(
            fields=self._validated(request, ser.TariffWriteSerializer, partial=False),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(
            success_envelope(dict(ser.TariffReadSerializer(dto).data)),
            status=status.HTTP_201_CREATED,
        )


class AdminTariffDetailView(_AdminView):
    @extend_schema(
        request=ser.TariffWriteSerializer,
        responses={200: ser.TariffReadSerializer},
        summary="Amend a metered transfer tariff",
        tags=_TAGS,
    )
    def patch(self, request: Request, public_id: UUID) -> Response:
        dto = services.update_tariff(
            public_id,
            fields=self._validated(request, ser.TariffWriteSerializer, partial=True),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(success_envelope(dict(ser.TariffReadSerializer(dto).data)))


class AdminQuotePreviewView(_AdminView):
    """§27.11: "a quote-preview tool for any origin-destination-class
    combination".

    A POST rather than a GET, and it changes nothing. The body carries a
    pickup instant and a party, which are awkward in a query string and would
    end up cached by something; more importantly, a preview whose URL could be
    bookmarked would eventually be pasted somewhere as a price.
    """

    @extend_schema(
        request=ser.QuotePreviewSerializer,
        responses={200: ser.QuotePreviewResultSerializer},
        summary="Preview what a route would be quoted at, and why",
        description=(
            "Runs the tourist's own SRS 12.4 resolution ladder and reports "
            "which rule answered and on which rung. A fare that fell through "
            "to the country default when a corridor was expected is a "
            "misconfiguration indistinguishable from a correct answer without "
            "that information."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        result = services.preview_quote(
            self._validated(request, ser.QuotePreviewSerializer, partial=False)
        )
        return Response(success_envelope(dict(ser.QuotePreviewResultSerializer(result).data)))


# -- §27.7 providers ----------------------------------------------------------

#: What a provider cannot be created without. The serializer marks every field
#: optional so one class serves create and PATCH; the difference is stated here.
_REQUIRED_ON_CREATE = (
    "legal_name",
    "trading_name",
    "provider_type",
    "contact_email",
    "contact_phone",
    "region",
)


class _ProviderAdminView(_AdminView):
    """§27.7: provider verification is COMPLIANCE_ADMIN's (§5.2).

    Not a `ScopedQuerysetMixin` view, and the §37.2 matrix records why: every
    role holding VERIFICATION_DECIDE holds `Scope.GLOBAL` over `PROVIDER`, so a
    filter would match every row while reporting a control.
    """

    permission_classes = PROVIDER_ADMIN_PERMISSIONS


class AdminProviderListView(_ProviderAdminView):
    @extend_schema(
        parameters=[
            OpenApiParameter("provider_type", str, required=False),
            OpenApiParameter("verify_status", str, required=False),
        ],
        responses={200: ser.ProviderReadSerializer(many=True)},
        summary="List providers",
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        rows = services.list_providers(
            provider_type=request.query_params.get("provider_type") or None,
            verify_status=request.query_params.get("verify_status") or None,
        )
        return Response(success_envelope(ser.ProviderReadSerializer(rows, many=True).data))

    @extend_schema(
        request=ser.ProviderWriteSerializer,
        responses={201: ser.ProviderReadSerializer},
        summary="Create a provider",
        description=(
            "Creates the provider in DRAFT. It cannot be sold until it is walked "
            "to VERIFIED through the status endpoint (BR-037)."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        fields = self._validated(request, ser.ProviderWriteSerializer, partial=False)
        missing = [name for name in _REQUIRED_ON_CREATE if name not in fields]
        if missing:
            raise ValidationError(
                "These fields are required.",
                details=[{"field": name, "issue": "required"} for name in missing],
            )
        dto = services.create_provider(
            fields=fields, principal=principal_from_request(request), ip=self._ip(request)
        )
        return Response(
            success_envelope(dict(ser.ProviderReadSerializer(dto).data)),
            status=status.HTTP_201_CREATED,
        )


class AdminProviderDetailView(_ProviderAdminView):
    @extend_schema(
        responses={200: ser.ProviderReadSerializer}, summary="Read a provider", tags=_TAGS
    )
    def get(self, request: Request, public_id: UUID) -> Response:
        dto = services.get_provider(public_id)
        return Response(success_envelope(dict(ser.ProviderReadSerializer(dto).data)))

    @extend_schema(
        request=ser.ProviderWriteSerializer,
        responses={200: ser.ProviderReadSerializer},
        summary="Amend a provider",
        tags=_TAGS,
    )
    def patch(self, request: Request, public_id: UUID) -> Response:
        dto = services.update_provider(
            public_id,
            fields=self._validated(request, ser.ProviderWriteSerializer, partial=True),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(success_envelope(dict(ser.ProviderReadSerializer(dto).data)))


class AdminProviderStatusView(_ProviderAdminView):
    @extend_schema(
        request=ser.ProviderStatusSerializer,
        responses={200: ser.ProviderStatusResultSerializer},
        summary="Move a provider through verification",
        description=(
            "VERIFIED and REJECTED walk SRS 26.2's declared edges and audit each "
            "step. SUSPENDED is taken from VERIFIED only. REJECTED and SUSPENDED "
            "require a reason."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        body = self._validated(request, ser.ProviderStatusSerializer, partial=False)
        result = services.change_provider_status(
            public_id,
            status=body["status"],
            reason=body.get("reason", ""),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(success_envelope(dict(ser.ProviderStatusResultSerializer(result).data)))


class AdminActivityProviderView(_AdminView):
    """Assigning a listing is a catalogue edit, so CATALOGUE_MANAGE gates it."""

    @extend_schema(
        request=ser.ActivityProviderSerializer,
        responses={200: ser.ProviderReadSerializer},
        summary="Set the provider that sells an activity",
        tags=_TAGS,
    )
    def put(self, request: Request, public_id: UUID) -> Response:
        body = self._validated(request, ser.ActivityProviderSerializer, partial=False)
        result = services.assign_activity_provider(
            public_id,
            provider_public_id=body["provider"],
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        provider = dict(ser.ProviderReadSerializer(result["provider"]).data)
        return Response(success_envelope(provider))


# -- §27.9 exceptional booking controls -------------------------------------------------------


class _SuperAdminView(_AdminView):
    """BR-038 names the role, not a permission: "only with the SUPER_ADMIN role"."""

    permission_classes = [
        IsAuthenticatedPrincipal,
        HasRole.for_(Role.SUPER_ADMIN),
        EmailVerified,
        MfaSatisfied,
    ]


class AdminBookingForceTransitionView(_SuperAdminView):
    @extend_schema(
        request=ser.ForceTransitionSerializer,
        responses={200: ser.ForceTransitionResultSerializer},
        summary="Force a booking along a declared transition",
        description=(
            "BR-038. Guards are bypassed; SRS 20.2's edges are not — an undeclared "
            "transition is 409 ILLEGAL_TRANSITION for a SUPER_ADMIN too. A reason "
            "is required and the action is always audited."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        body = self._validated(request, ser.ForceTransitionSerializer, partial=False)
        principal = principal_from_request(request)
        assert principal is not None
        result = services.force_booking_transition(
            public_id,
            target=body["status"],
            reason=body["reason"],
            principal=principal,
            ip=self._ip(request),
        )
        return Response(success_envelope(dict(ser.ForceTransitionResultSerializer(result).data)))


class AdminBookingVoucherReissueView(_SuperAdminView):
    @extend_schema(
        request=ser.ReissueVoucherSerializer,
        responses={201: ser.ReissueVoucherResultSerializer},
        summary="Re-issue a booking's voucher",
        tags=_TAGS,
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        body = self._validated(request, ser.ReissueVoucherSerializer, partial=False)
        principal = principal_from_request(request)
        assert principal is not None
        result = services.reissue_booking_voucher(
            public_id, reason=body["reason"], principal=principal, ip=self._ip(request)
        )
        return Response(
            success_envelope(dict(ser.ReissueVoucherResultSerializer(result).data)),
            status=status.HTTP_201_CREATED,
        )


# -- §21.6, §27.10: refunds and the payment console -----------------------------------------


class _FinanceView(_AdminView):
    """Finance reads money; §5.2 gives FINANCE_OFFICER `FINANCE_READ` globally.

    SUPER_ADMIN holds every permission, so the console is reachable by both
    without naming roles here — a role list would drift from §5.2's table the
    first time somebody added a role.
    """

    permission_classes = [
        IsAuthenticatedPrincipal,
        HasPermission.for_(Permission.FINANCE_READ),
        EmailVerified,
        MfaSatisfied,
    ]


class AdminPaymentListView(_FinanceView):
    @extend_schema(
        responses={200: ser.AdminPaymentSerializer(many=True)},
        summary="Search payments",
        parameters=[
            OpenApiParameter("status", str, description="§21.4 status to filter by"),
        ],
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        rows = services.list_payments(status=request.query_params.get("status") or None)
        return Response(success_envelope(ser.AdminPaymentSerializer(rows, many=True).data))


class AdminRefundListCreateView(_FinanceView):
    """§27.10's refund queue, and §9.3.7's `POST /refunds`."""

    @extend_schema(
        responses={200: ser.RefundReadSerializer(many=True)},
        summary="Refund queue",
        parameters=[
            OpenApiParameter("status", str, description="REQUESTED, SETTLED or FAILED"),
        ],
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        rows = services.list_refunds(status=request.query_params.get("status") or None)
        return Response(success_envelope(ser.RefundReadSerializer(rows, many=True).data))

    @extend_schema(
        request=ser.RefundRequestSerializer,
        responses={201: ser.RefundReadSerializer},
        summary="Issue a discretionary refund",
        description=(
            "§21.6. A refund following a cancellation policy is written "
            "automatically and never passes through here. BR-047: above "
            "`refund.auto_approve_limit` the caller must hold REFUND_APPROVE."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        body = self._validated(request, ser.RefundRequestSerializer, partial=False)
        refund = services.issue_refund(
            payment_public_id=body["payment"],
            amount=body["amount"],
            reason_code=body["reason_code"],
            reason=body.get("reason", ""),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(
            success_envelope(ser.RefundReadSerializer(refund).data),
            status=status.HTTP_201_CREATED,
        )


# -- §22.2, §22.5, §26.7: the finance console ------------------------------------------------


class AdminCommissionRuleListCreateView(_AdminView):
    """§27.11's commercial rules. `/admin/commission-rules`, §9.3.10.

    CATALOGUE_MANAGE rather than FINANCE_READ: a commission rule is a
    commercial term somebody negotiates, and §5.2 gives that to the same
    administrators who manage listings and tariffs. Finance approves what is
    *paid*; the console that sets rates is the one that sets prices.
    """

    permission_classes = CATALOGUE_ADMIN_PERMISSIONS

    @extend_schema(
        responses={200: ser.CommissionRuleReadSerializer(many=True)},
        summary="Commission rules",
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        rules = services.list_commission_rules()
        return Response(success_envelope(ser.CommissionRuleReadSerializer(rules, many=True).data))

    @extend_schema(
        request=ser.CommissionRuleWriteSerializer,
        responses={201: ser.CommissionRuleReadSerializer},
        summary="Add a commission rule",
        description=(
            "§22.2. A rule cannot reach a booking already sold: BR-070 freezes "
            "the rate on the booking at confirmation (TC-110)."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        body = self._validated(request, ser.CommissionRuleWriteSerializer, partial=False)
        rule = services.create_commission_rule(
            principal=principal_from_request(request),
            ip=self._ip(request),
            **body,
        )
        return Response(
            success_envelope(ser.CommissionRuleReadSerializer(rule).data),
            status=status.HTTP_201_CREATED,
        )


class AdminCommissionRuleDetailView(_AdminView):
    permission_classes = CATALOGUE_ADMIN_PERMISSIONS

    @extend_schema(
        request=ser.CommissionRuleWriteSerializer,
        responses={200: ser.CommissionRuleReadSerializer},
        summary="Change a commission rule",
        tags=_TAGS,
    )
    def patch(self, request: Request, public_id: UUID) -> Response:
        body = self._validated(request, ser.CommissionRuleWriteSerializer, partial=True)
        body.pop("provider", None)
        rule = services.update_commission_rule(
            public_id,
            principal=principal_from_request(request),
            ip=self._ip(request),
            **body,
        )
        return Response(success_envelope(ser.CommissionRuleReadSerializer(rule).data))


class AdminPayoutListView(_FinanceView):
    """§9.3.10's `GET /admin/payouts`."""

    @extend_schema(
        responses={200: ser.PayoutSerializer(many=True)},
        summary="Payout batches",
        parameters=[OpenApiParameter("status", str, description="DRAFT, APPROVED, PAID or FAILED")],
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        payouts = services.list_payouts(status=request.query_params.get("status") or None)
        return Response(success_envelope(ser.PayoutSerializer(payouts, many=True).data))


class AdminPayoutApproveView(_FinanceView):
    """§9.3.10's `POST /admin/payouts/{id}/approve` — BR-075.

    Gated on REFUND_APPROVE's sibling, PAYOUT_APPROVE, which §5.2 gives to
    FINANCE_OFFICER and SUPER_ADMIN alone.
    """

    permission_classes = [
        IsAuthenticatedPrincipal,
        HasPermission.for_(Permission.PAYOUT_APPROVE),
        EmailVerified,
        MfaSatisfied,
    ]

    @extend_schema(
        request=None,
        responses={200: ser.PayoutSerializer},
        summary="Approve a payout",
        tags=_TAGS,
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        payout = services.approve_payout(
            public_id, principal=principal_from_request(request), ip=self._ip(request)
        )
        return Response(success_envelope(ser.PayoutSerializer(payout).data))


class AdminPayoutReleaseView(AdminPayoutApproveView):
    """`POST /admin/payouts/{id}/release`.

    §9.3.10 names approval and not release, and §22.5 describes both — so the
    route exists under the same permission. **No money moves** (ADR 0028
    decision 6): the reference recorded is a transfer somebody made at their
    bank, and the ledger entry is what says the obligation has left.
    """

    @extend_schema(
        request=ser.PayoutReleaseSerializer,
        responses={200: ser.PayoutSerializer},
        summary="Record a payout as paid",
        description=(
            "No transfer is made: the payout rail is Phase 11's, behind a port "
            "with no adapter. `rail_reference` is how the transfer that was "
            "made by hand can be found again."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        body = self._validated(request, ser.PayoutReleaseSerializer, partial=False)
        payout = services.release_payout(
            public_id,
            rail_reference=body["rail_reference"],
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(success_envelope(ser.PayoutSerializer(payout).data))


class AdminProviderEarningsView(_FinanceView):
    """§26.7's figures, for an administrator.

    The provider portal is Phase 11 and there is no provider principal yet, so
    an operator asking "what am I owed" is answered by somebody at Pumba
    reading this — which is the honest version of the screen until they can
    read it themselves.
    """

    @extend_schema(
        responses={200: ser.ProviderEarningsSerializer},
        summary="What a provider has earned and been paid",
        tags=_TAGS,
    )
    def get(self, request: Request, public_id: UUID) -> Response:
        earnings = services.provider_earnings(public_id)
        return Response(success_envelope(ser.ProviderEarningsSerializer(earnings).data))


class AdminFinanceReportView(_FinanceView):
    """§22.7's daily figures — from the ledger, never from the booking table."""

    @extend_schema(
        responses={200: ser.FinanceReportSerializer},
        summary="Financial position, by account",
        parameters=[OpenApiParameter("currency", str, description="Narrow to one currency")],
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        report = services.finance_report(currency=request.query_params.get("currency") or None)
        return Response(success_envelope(ser.FinanceReportSerializer(report).data))
