"""Geography hierarchy invariants — SRS §4.1, §4.2, §7.5.6, §41.12.

Zanzibar and Arusha are both tested, from the same code path with different
data. That pairing is the §4.2 discipline made checkable: if anything here
knew about Zanzibar, Arusha would need a change.
"""

from __future__ import annotations

import pytest

from apps.catalogue.domain.hierarchy import (
    DestinationFlags,
    GatewayType,
    HierarchyError,
    validate_country_code,
    validate_currency_code,
    validate_gateway,
    validate_timezone,
)


class TestCountryCode:
    def test_tanzania(self) -> None:
        assert validate_country_code("TZ") == "TZ"

    def test_it_upper_cases(self) -> None:
        assert validate_country_code("tz") == "TZ"

    def test_it_trims(self) -> None:
        assert validate_country_code(" tz ") == "TZ"

    @pytest.mark.parametrize("code", ["T", "TZA", "", "T1", "T-"])
    def test_malformed_codes_are_rejected(self, code: str) -> None:
        with pytest.raises(HierarchyError, match="two ASCII letters"):
            validate_country_code(code)

    def test_a_non_ascii_lookalike_is_rejected(self) -> None:
        # Cyrillic U+0422 and Latin T are indistinguishable on screen, so
        # the code point is written out rather than pasted.
        with pytest.raises(HierarchyError, match="two ASCII letters"):
            validate_country_code(chr(0x422) + "Z")  # Cyrillic TE, built by code point

    def test_an_unrecognised_but_wellformed_code_is_accepted(self) -> None:
        # Structural only: a market opening in a newly recognised country must
        # not need a release to add its code to a hard-coded list.
        assert validate_country_code("XK") == "XK"


class TestCurrencyCode:
    def test_tanzanian_shilling(self) -> None:
        assert validate_currency_code("tzs") == "TZS"

    @pytest.mark.parametrize("code", ["TZ", "TZSX", "", "TZ1"])
    def test_malformed_codes_are_rejected(self, code: str) -> None:
        with pytest.raises(HierarchyError, match="three ASCII letters"):
            validate_currency_code(code)

    def test_other_market_currencies_work_unchanged(self) -> None:
        for code in ("USD", "EUR", "KES", "RWF", "UGX"):
            assert validate_currency_code(code) == code


class TestTimezone:
    def test_the_seed_zone(self) -> None:
        assert validate_timezone("Africa/Dar_es_Salaam") == "Africa/Dar_es_Salaam"

    def test_arusha_needs_no_code_change(self) -> None:
        # §41.12's acceptance destination. Arusha is on the mainland and
        # shares Tanzania's zone; the name Africa/Arusha is not in the tz
        # database, which is exactly why this checks it rather than a regex.
        assert validate_timezone("Africa/Dar_es_Salaam") == "Africa/Dar_es_Salaam"

    def test_a_distant_zone_works(self) -> None:
        assert validate_timezone("Pacific/Auckland") == "Pacific/Auckland"

    def test_a_plausible_but_nonexistent_zone_is_rejected(self) -> None:
        # "Africa/Zanzibar" looks exactly like a zone and is not one. A regex
        # would pass it, and it would then fail at render time inside every
        # opening-hours table in that destination.
        with pytest.raises(HierarchyError, match="not a known IANA time zone"):
            validate_timezone("Africa/Zanzibar")

    def test_an_empty_zone_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="timezone is required"):
            validate_timezone("  ")

    def test_a_utc_offset_is_not_a_zone(self) -> None:
        # +03:00 loses the DST rules a zone carries, which is the whole reason
        # §4.1 stores an IANA name.
        with pytest.raises(HierarchyError, match="not a known IANA time zone"):
            validate_timezone("+03:00")

    def test_it_trims(self) -> None:
        assert validate_timezone("  Africa/Nairobi  ") == "Africa/Nairobi"


class TestGatewayCoherence:
    def test_znz_is_a_valid_airport_gateway(self) -> None:
        assert validate_gateway(is_gateway=True, gateway_type="AIRPORT", gateway_code="ZNZ") == (
            GatewayType.AIRPORT,
            "ZNZ",
        )

    def test_a_non_gateway_carries_neither_field(self) -> None:
        assert validate_gateway(is_gateway=False, gateway_type=None, gateway_code=None) == (
            None,
            None,
        )

    def test_a_gateway_without_a_type_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="needs a gateway_type"):
            validate_gateway(is_gateway=True, gateway_type=None, gateway_code="ZNZ")

    def test_a_gateway_without_a_code_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="needs a gateway_code"):
            validate_gateway(is_gateway=True, gateway_type="AIRPORT", gateway_code=None)

    def test_a_blank_code_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="needs a gateway_code"):
            validate_gateway(is_gateway=True, gateway_type="AIRPORT", gateway_code="   ")

    def test_a_code_on_a_non_gateway_is_rejected(self) -> None:
        # The reverse direction matters: §7.5.6 indexes
        # UNIQUE(gateway_code) WHERE is_gateway, so an orphaned code sits
        # outside that index and a later gateway can claim the same one.
        with pytest.raises(HierarchyError, match="only meaningful when is_gateway"):
            validate_gateway(is_gateway=False, gateway_type=None, gateway_code="ZNZ")

    def test_a_type_on_a_non_gateway_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="only meaningful when is_gateway"):
            validate_gateway(is_gateway=False, gateway_type="AIRPORT", gateway_code=None)

    def test_an_unknown_gateway_type_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="gateway_type must be one of"):
            validate_gateway(is_gateway=True, gateway_type="HELIPAD", gateway_code="ZNZ")

    @pytest.mark.parametrize("kind", ["AIRPORT", "SEAPORT", "LAND_BORDER"])
    def test_every_srs_gateway_type_is_accepted(self, kind: str) -> None:
        parsed, _ = validate_gateway(is_gateway=True, gateway_type=kind, gateway_code="X1")
        assert parsed.value == kind

    def test_the_type_and_code_are_normalised(self) -> None:
        assert validate_gateway(
            is_gateway=True, gateway_type=" airport ", gateway_code=" znz "
        ) == (GatewayType.AIRPORT, "ZNZ")

    def test_a_code_longer_than_the_column_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="up to 10 alphanumeric"):
            validate_gateway(is_gateway=True, gateway_type="SEAPORT", gateway_code="A" * 11)

    def test_a_non_alphanumeric_code_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="up to 10 alphanumeric"):
            validate_gateway(is_gateway=True, gateway_type="SEAPORT", gateway_code="ZN-Z")

    def test_a_non_iata_seaport_code_is_accepted(self) -> None:
        # ZNZ is IATA, but a ferry terminal or land border may use anything.
        parsed, code = validate_gateway(
            is_gateway=True, gateway_type="SEAPORT", gateway_code="MALINDI1"
        )
        assert (parsed, code) == (GatewayType.SEAPORT, "MALINDI1")


class TestDestinationFlags:
    def test_the_seed_gateway_builds(self) -> None:
        flags = DestinationFlags.build(
            timezone="Africa/Dar_es_Salaam",
            default_currency="TZS",
            is_gateway=True,
            gateway_type="AIRPORT",
            gateway_code="ZNZ",
        )
        assert flags.gateway_type is GatewayType.AIRPORT
        assert flags.gateway_code == "ZNZ"
        assert flags.feature_rank == 100

    def test_arusha_builds_from_the_same_code_path(self) -> None:
        # §41.12: no application code change, no migration, no deployment.
        flags = DestinationFlags.build(timezone="Africa/Dar_es_Salaam", default_currency="TZS")
        assert flags.timezone == "Africa/Dar_es_Salaam"
        assert flags.is_gateway is False
        assert flags.gateway_code is None

    def test_a_destination_in_another_country_and_currency_builds(self) -> None:
        flags = DestinationFlags.build(timezone="Europe/Paris", default_currency="eur")
        assert flags.default_currency == "EUR"

    def test_a_zero_feature_rank_is_rejected(self) -> None:
        # `ranking` sorts feature_rank ascending, so 0 would place a
        # destination above every curated one — silently and permanently.
        with pytest.raises(HierarchyError, match="positive integer"):
            DestinationFlags.build(
                timezone="Africa/Dar_es_Salaam", default_currency="TZS", feature_rank=0
            )

    def test_a_negative_feature_rank_is_rejected(self) -> None:
        with pytest.raises(HierarchyError, match="positive integer"):
            DestinationFlags.build(
                timezone="Africa/Dar_es_Salaam", default_currency="TZS", feature_rank=-5
            )

    def test_an_incoherent_gateway_is_rejected_at_construction(self) -> None:
        with pytest.raises(HierarchyError, match="needs a gateway_code"):
            DestinationFlags.build(
                timezone="Africa/Dar_es_Salaam",
                default_currency="TZS",
                is_gateway=True,
                gateway_type="AIRPORT",
            )

    def test_an_invalid_timezone_is_rejected_at_construction(self) -> None:
        with pytest.raises(HierarchyError, match="not a known IANA time zone"):
            DestinationFlags.build(timezone="Africa/Zanzibar", default_currency="TZS")

    def test_flags_are_immutable(self) -> None:
        flags = DestinationFlags.build(timezone="Africa/Dar_es_Salaam", default_currency="TZS")
        with pytest.raises(AttributeError):
            flags.is_gateway = True  # type: ignore[misc]


class TestNoDestinationNamesInCode:
    def test_the_module_names_no_destination_country_or_currency(self) -> None:
        # §4.2's prohibited list, asserted rather than trusted.
        import inspect
        import io
        import tokenize

        from apps.catalogue.domain import hierarchy

        executable = " ".join(
            token.string
            for token in tokenize.generate_tokens(
                io.StringIO(inspect.getsource(hierarchy)).readline
            )
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )
        for name in ("Zanzibar", "ZANZIBAR", "Unguja", "Pemba", "TZS", "ZNZ", "Tanzania"):
            assert name not in executable, f"{name} appears in executable code"
