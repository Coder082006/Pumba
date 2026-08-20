"""Cancellation policies — SRS §14.6, §27.12.

The four §14.6 policies are expressed here as data and asserted to behave as
the prose describes. That is the point: if any of them needed a branch in this
module, the generic tier form would have failed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.catalogue.domain.cancellation import (
    CancellationPolicyError,
    Tier,
    parse_tiers,
    refund_percent_at,
    validate_tiers,
)

# §14.6, as rows rather than classes.
FLEX_48H = validate_tiers([Tier(48, Decimal(100))])
MODERATE_7D = validate_tiers([Tier(168, Decimal(100)), Tier(48, Decimal(50))])
STRICT_14D = validate_tiers([Tier(336, Decimal(50))])
NON_REFUNDABLE: tuple[Tier, ...] = validate_tiers([])


class TestTheFourSeedPolicies:
    """Each assertion is one clause of §14.6's prose."""

    def test_flex_48h_refunds_in_full_more_than_48_hours_out(self) -> None:
        assert refund_percent_at(FLEX_48H, hours_before=Decimal(72)) == Decimal(100)

    def test_flex_48h_refunds_nothing_thereafter(self) -> None:
        assert refund_percent_at(FLEX_48H, hours_before=Decimal(24)) == Decimal(0)

    def test_moderate_7d_refunds_in_full_beyond_seven_days(self) -> None:
        assert refund_percent_at(MODERATE_7D, hours_before=Decimal(200)) == Decimal(100)

    def test_moderate_7d_refunds_half_between_seven_days_and_48_hours(self) -> None:
        assert refund_percent_at(MODERATE_7D, hours_before=Decimal(100)) == Decimal(50)

    def test_moderate_7d_refunds_nothing_inside_48_hours(self) -> None:
        assert refund_percent_at(MODERATE_7D, hours_before=Decimal(12)) == Decimal(0)

    def test_strict_14d_refunds_half_beyond_fourteen_days(self) -> None:
        assert refund_percent_at(STRICT_14D, hours_before=Decimal(400)) == Decimal(50)

    def test_strict_14d_refunds_nothing_thereafter(self) -> None:
        assert refund_percent_at(STRICT_14D, hours_before=Decimal(300)) == Decimal(0)

    def test_non_refundable_refunds_nothing_at_any_time(self) -> None:
        # The cleanest evidence the generic form is right: the strictest policy
        # in the catalogue is the empty tier list.
        for hours in (Decimal(0), Decimal(48), Decimal(10_000)):
            assert refund_percent_at(NON_REFUNDABLE, hours_before=hours) == Decimal(0)


class TestTheBoundaryIsStrict:
    def test_exactly_48_hours_is_not_more_than_48_hours(self) -> None:
        # §14.6: "more than 48 h before check-in". Reading this inclusively
        # hands back money the policy does not offer, on the single case most
        # likely to be disputed.
        assert refund_percent_at(FLEX_48H, hours_before=Decimal(48)) == Decimal(0)

    def test_a_minute_past_48_hours_still_refunds(self) -> None:
        assert refund_percent_at(FLEX_48H, hours_before=Decimal("48.017")) == Decimal(100)

    def test_a_minute_inside_48_hours_does_not(self) -> None:
        assert refund_percent_at(FLEX_48H, hours_before=Decimal("47.983")) == Decimal(0)

    def test_fractional_hours_are_not_rounded_into_a_tier(self) -> None:
        # 47.9 hours is inside the window. Rounding to 48 would be the same
        # class of mistake as using a float for money.
        assert refund_percent_at(FLEX_48H, hours_before=Decimal("47.9")) == Decimal(0)

    def test_the_moderate_boundary_at_seven_days_is_strict_too(self) -> None:
        assert refund_percent_at(MODERATE_7D, hours_before=Decimal(168)) == Decimal(50)


class TestEdges:
    def test_zero_hours_before_falls_to_the_last_tier(self) -> None:
        assert refund_percent_at(FLEX_48H, hours_before=Decimal(0)) == Decimal(0)

    def test_a_cancellation_after_the_service_started_refunds_nothing(self) -> None:
        assert refund_percent_at(MODERATE_7D, hours_before=Decimal(-3)) == Decimal(0)

    def test_a_very_early_cancellation_gets_the_most_generous_tier(self) -> None:
        assert refund_percent_at(MODERATE_7D, hours_before=Decimal(100_000)) == Decimal(100)

    def test_falling_off_the_end_is_zero_not_an_error(self) -> None:
        # Every policy implicitly ends with "and nothing thereafter".
        assert refund_percent_at(STRICT_14D, hours_before=Decimal(1)) == Decimal(0)


class TestTierValidation:
    def test_a_tier_at_zero_hours_is_allowed(self) -> None:
        assert validate_tiers([Tier(0, Decimal(10))])[0].hours_before == 0

    def test_negative_hours_are_rejected(self) -> None:
        with pytest.raises(CancellationPolicyError, match="cannot be negative"):
            Tier(-1, Decimal(100))

    def test_a_refund_above_one_hundred_percent_is_rejected(self) -> None:
        with pytest.raises(CancellationPolicyError, match="between 0 and 100"):
            Tier(48, Decimal(101))

    def test_a_negative_refund_is_rejected(self) -> None:
        with pytest.raises(CancellationPolicyError, match="between 0 and 100"):
            Tier(48, Decimal(-1))

    def test_a_fractional_refund_percent_is_allowed(self) -> None:
        assert validate_tiers([Tier(48, Decimal("12.5"))])[0].refund_percent == Decimal("12.5")

    def test_duplicate_thresholds_are_rejected(self) -> None:
        # Two rules for the same moment, and no way to tell which was meant.
        with pytest.raises(CancellationPolicyError, match="duplicate hours_before"):
            validate_tiers([Tier(48, Decimal(100)), Tier(48, Decimal(50))])

    def test_out_of_order_tiers_are_rejected_not_sorted(self) -> None:
        # Sorting silently would hide a typo that reads as a far more generous
        # policy than the administrator intended.
        with pytest.raises(CancellationPolicyError, match="descending"):
            validate_tiers([Tier(48, Decimal(50)), Tier(168, Decimal(100))])

    def test_an_increasing_refund_is_rejected(self) -> None:
        # Every §14.6 policy decreases. An increasing one is data entry, not
        # commerce; if a market ever wants one it should arrive deliberately.
        with pytest.raises(CancellationPolicyError, match="must not increase"):
            validate_tiers([Tier(168, Decimal(50)), Tier(48, Decimal(100))])

    def test_an_empty_policy_is_valid(self) -> None:
        assert validate_tiers([]) == ()

    def test_equal_consecutive_percents_are_allowed(self) -> None:
        # Two thresholds with the same refund is redundant but not wrong, and
        # rejecting it would block a policy an administrator may be building up
        # to in stages.
        assert len(validate_tiers([Tier(168, Decimal(50)), Tier(48, Decimal(50))])) == 2

    def test_validation_returns_a_tuple_so_it_cannot_be_mutated_afterwards(self) -> None:
        assert isinstance(validate_tiers([Tier(48, Decimal(100))]), tuple)


class TestParsingTheJsonbColumn:
    """`cancellation_policy.tiers` is administrator-supplied JSON."""

    def test_the_seed_shaped_policy_parses(self) -> None:
        tiers = parse_tiers(
            [
                {"hours_before": 168, "refund_percent": 100},
                {"hours_before": 48, "refund_percent": 50},
            ]
        )
        assert tiers == (
            Tier(hours_before=168, refund_percent=Decimal(100)),
            Tier(hours_before=48, refund_percent=Decimal(50)),
        )

    def test_an_empty_list_is_a_policy_that_refunds_nothing(self) -> None:
        assert parse_tiers([]) == ()
        assert refund_percent_at(parse_tiers([]), hours_before=Decimal(1000)) == Decimal(0)

    def test_null_is_read_as_no_tiers(self) -> None:
        assert parse_tiers(None) == ()

    def test_a_percentage_arriving_as_a_float_becomes_an_exact_decimal(self) -> None:
        """psycopg hands back a bare JSON number as a float. §18.5: never a
        float, anywhere, for any reason - and 50.1 is not representable."""
        (tier,) = parse_tiers([{"hours_before": 48, "refund_percent": 50.1}])
        assert tier.refund_percent == Decimal("50.1")

    def test_a_string_percentage_is_accepted(self) -> None:
        (tier,) = parse_tiers([{"hours_before": 48, "refund_percent": "50.00"}])
        assert tier.refund_percent == Decimal("50.00")

    def test_an_unknown_key_is_rejected_rather_than_ignored(self) -> None:
        """A policy carrying `refund_pct` is a policy somebody believes says
        something it does not."""
        with pytest.raises(CancellationPolicyError, match="unknown keys"):
            parse_tiers([{"hours_before": 48, "refund_percent": 50, "refund_pct": 90}])

    def test_a_missing_key_is_rejected(self) -> None:
        with pytest.raises(CancellationPolicyError, match="missing"):
            parse_tiers([{"hours_before": 48}])

    def test_a_non_list_is_rejected(self) -> None:
        with pytest.raises(CancellationPolicyError, match="must be a list"):
            parse_tiers({"hours_before": 48, "refund_percent": 50})

    def test_a_non_object_tier_is_rejected(self) -> None:
        with pytest.raises(CancellationPolicyError, match="must be an object"):
            parse_tiers([[48, 50]])

    def test_a_boolean_is_not_a_number_of_hours(self) -> None:
        with pytest.raises(CancellationPolicyError, match="whole number"):
            parse_tiers([{"hours_before": True, "refund_percent": 50}])

    def test_a_fractional_hours_before_is_rejected(self) -> None:
        with pytest.raises(CancellationPolicyError, match="whole number"):
            parse_tiers([{"hours_before": 47.5, "refund_percent": 50}])

    def test_an_unparseable_percentage_is_rejected(self) -> None:
        with pytest.raises(CancellationPolicyError, match="not a number"):
            parse_tiers([{"hours_before": 48, "refund_percent": "half"}])

    def test_the_ordering_rules_still_apply_after_parsing(self) -> None:
        with pytest.raises(CancellationPolicyError, match="descending"):
            parse_tiers(
                [
                    {"hours_before": 48, "refund_percent": 50},
                    {"hours_before": 168, "refund_percent": 100},
                ]
            )


class TestNoPolicyNamesAppearInCode:
    def test_the_module_contains_no_policy_codes(self) -> None:
        # §4.2 and §14.6: policies are rows. A code appearing in the module
        # source means somebody special-cased one.
        import inspect
        import io
        import tokenize

        from apps.catalogue.domain import cancellation

        source = inspect.getsource(cancellation)
        # Tokenise rather than filter lines: the codes are named in the module
        # docstring on purpose, and a docstring is not a branch.
        executable = " ".join(
            token.string
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )
        for code in ("FLEX_48H", "MODERATE_7D", "STRICT_14D", "NON_REFUNDABLE"):
            assert code not in executable, f"{code} is branched on in code"
