"""Settings read port tests — NFR-M07, SRS Appendix B."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.common import config
from apps.common.config import SETTINGS_REGISTER, UnknownSettingError, get_setting


@pytest.fixture(autouse=True)
def _clear_provider():
    config._provider = None
    yield
    config._provider = None


class TestRegister:
    #: Appendix B verbatim — 19 rows in the first table, 11 in the second.
    APPENDIX_B = frozenset(
        {
            "buffer.arrival_processing_minutes",
            "buffer.airport_departure_minutes",
            "buffer.activity_minutes",
            "quote.ttl_minutes",
            "payment.window_minutes",
            "dispatch.lead_hours",
            "offer.ttl_seconds.scheduled",
            "offer.ttl_seconds.imminent",
            "assignment.disclosure_hours",
            "dispatch.weights",
            "dispatch.max_radius_m",
            "geofence.pickup_m",
            "geofence.approach_m",
            "wait.airport_minutes",
            "wait.standard_minutes",
            "platform_fee_rate",
            "commission.default_percent",
            "fx.markup_percent",
            "settlement_hold_days",
            "payout.minimum",
            "refund.auto_approve_limit",
            "limit.items_per_day",
            "limit.travel_minutes_per_day",
            "trip.max_days",
            "stay.max_nights",
            "availability.horizon_days",
            "departures.horizon_days",
            "location.retention_days",
            "provider_response_hours",
            "review.window_days",
        }
    )

    def test_every_appendix_b_key_is_declared(self) -> None:
        """By name rather than by count — a count passes while the wrong
        thirty keys are present."""
        assert set(SETTINGS_REGISTER) >= self.APPENDIX_B

    def test_the_extension_beyond_appendix_b_stays_visible(self) -> None:
        """ADR 0006: the register has deliberately diverged from Appendix B.

        Every added key must be namespaced, so the divergence is legible in
        the register itself and a stray unprefixed key cannot slip in
        claiming to be from the appendix.

        The namespaces, and what each was added for:

            auth.       Phase 2, §30.2's credential and session policy.
            ratelimit.  Phase 2, §9.6's limits.
            page.       Phase 3, §9.1's `?limit`. A page size is a business
                        constant like any other — an administrator lowering
                        the ceiling during an incident is the case NFR-M07
                        exists for, and a hard-coded 100 would need a
                        deployment to change.
            search.     Phase 3, §24.7's `GET /search`. The two-character
                        minimum is quoted from the SRS, and the ceiling on
                        results per table is the difference between a search
                        box and a way to read the whole catalogue.
            review.     Appendix B already registers `review.window_days`, so
                        the namespace is not new; `review.min_display_count`
                        is. BR-127 says a subject with fewer than three
                        published reviews shows "New" rather than a mean, and
                        the three is a judgement about statistical confidence
                        rather than a law — a market with thinner supply may
                        want it lower. ADR 0015.
            client.     Phase 3, §23.13. `min_supported_version`, the floor a
                        client is forced above — a setting rather than a
                        constant because raising it is how a broken client
                        generation is retired, and that must not need an API
                        deployment.
            currency.   Phase 3, §24.1. Which currencies a tourist may choose.
            feature.    Phase 3, §35 — the flag set served through GET /config.
                        Open by construction: everything under this prefix is
                        public, which is safe only because the prefix means
                        "client-visible switch" and nothing else.
                        `tests/test_public_config.py` enforces that by
                        requiring every default here to be a bool, so a
                        threshold cannot hide under it.
            routing.    Phase 4, ADR 0019 / §12.6. The road factor and speed
                        model behind an APPROXIMATE travel estimate. §12.6
                        calls both "configurable" and gives their defaults but
                        Appendix B never names the keys, so they are coined
                        here. They are the most challengeable numbers in the
                        planner — Zanzibar's roads are not uniform and a single
                        factor is wrong in both directions in different places —
                        and correcting one against the first real routed leg
                        must not need a deployment.
            map.        Phase 3, ADR 0016 / Appendix D9. The tile URL and its
                        attribution string. Held as settings so changing map
                        provider is an administrator action rather than a
                        deployment, and paired so a swap cannot change the URL
                        without the attribution — which is a licence term, not
                        decoration.

        Adding a namespace is a deliberate edit to this tuple, which is the
        point: it is where somebody notices that a new family of settings has
        appeared.
        """
        extension = set(SETTINGS_REGISTER) - self.APPENDIX_B
        unnamespaced = {
            k
            for k in extension
            if not k.startswith(
                (
                    "auth.",
                    "ratelimit.",
                    "page.",
                    "search.",
                    "review.",
                    "map.",
                    "client.",
                    "currency.",
                    "feature.",
                    "routing.",
                )
            )
        }
        assert not unnamespaced, f"undocumented settings keys: {sorted(unnamespaced)}"

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("quote.ttl_minutes", 20),
            ("payment.window_minutes", 30),
            ("platform_fee_rate", Decimal("0.05")),
            ("commission.default_percent", Decimal("15")),
            ("dispatch.lead_hours", 72),
            ("geofence.pickup_m", 300),
            ("trip.max_days", 30),
            ("routing.road_factor", Decimal("1.35")),
            ("routing.average_speed_kmh", 45),
            ("location.retention_days", 30),
        ],
    )
    def test_defaults_match_the_srs(self, key: str, expected: object) -> None:
        assert get_setting(key) == expected

    def test_money_defaults_are_decimal_not_float(self) -> None:
        for key in ("platform_fee_rate", "commission.default_percent", "fx.markup_percent"):
            assert isinstance(get_setting(key), Decimal), f"{key} must be Decimal"

    def test_dispatch_weights_sum_to_one(self) -> None:
        """SRS §11.6 / Appendix B: 0.40/0.25/0.20/0.10/0.05.

        A weight vector that does not sum to 1.0 silently changes the meaning
        of every driver score.
        """
        assert sum(get_setting("dispatch.weights").values()) == Decimal("1.00")


class TestResolution:
    def test_unknown_key_raises_rather_than_returning_none(self) -> None:
        """A typo must not silently disable a business rule."""
        with pytest.raises(UnknownSettingError, match="not in the settings register"):
            get_setting("quote.ttl_minutes_typo")

    def test_registered_provider_overrides_the_default(self) -> None:
        config.register_provider(lambda key: 5 if key == "quote.ttl_minutes" else (_ for _ in ()))
        assert get_setting("quote.ttl_minutes") == 5

    def test_provider_lookup_error_falls_back_to_the_default(self) -> None:
        def provider(key: str) -> object:
            raise LookupError(key)

        config.register_provider(provider)
        assert get_setting("quote.ttl_minutes") == 20

    def test_provider_failure_falls_back_rather_than_propagating(self) -> None:
        """A settings-store outage must not take down request handling."""

        def provider(key: str) -> object:
            raise RuntimeError("redis down")

        config.register_provider(provider)
        assert get_setting("quote.ttl_minutes") == 20
