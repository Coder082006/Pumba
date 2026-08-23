"""`GET /config` — SRS §23.13, §24.1, §35.

The endpoint reads `system_setting`, and that table holds every business
constant in Appendix B: §11.6's dispatch weights, §30.14's fraud thresholds,
commission percentages, quote TTLs, the §30.2 lockout policy. Serving it
wholesale to an unauthenticated caller would publish the platform's operating
model — the dispatch weights are gameable by a provider who knows them, and the
fraud thresholds are exactly what an attacker needs in order to stay underneath.

So the tests that matter here are not "does it return the map URL". They are
the ones asserting that **nothing else can ever come out of it**, including a
setting that does not exist yet. `apps.common.public_config` is a closed
allow-list plus one prefix rule, and this file is what stops either becoming
advisory.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.common import config as config_module
from apps.common.config import SETTINGS_REGISTER
from apps.common.public_config import (
    FEATURE_FLAG_PREFIX,
    PUBLIC_SETTINGS,
    feature_flag_keys,
    public_config,
)

CONFIG_URL = "/api/v1/config"


@pytest.fixture
def public() -> APIClient:
    return APIClient()


def _body(client: APIClient) -> dict[str, Any]:
    response = client.get(CONFIG_URL)
    assert response.status_code == 200, response.data
    return dict(response.data["data"])


class TestTheEndpointServesWhatSection241Asks:
    def test_it_needs_no_authentication(self, public: APIClient) -> None:
        """§24.1 resolves configuration *before showing anything else*, and a
        version floor only reachable after login could never retire a broken
        client generation."""
        assert public.get(CONFIG_URL).status_code == 200

    def test_it_carries_the_splash_payload(self, public: APIClient) -> None:
        body = _body(public)
        assert body["min_supported_version"]
        assert isinstance(body["enabled_currencies"], list)
        assert isinstance(body["features"], dict)

    def test_it_carries_the_tile_url_and_its_attribution_together(self, public: APIClient) -> None:
        """ADR 0016: attribution is a licence term of every provider worth
        using, so a client that has the URL has the string it must render."""
        body = _body(public)
        assert body["map_tile_url"]
        assert body["map_tile_attribution"]

    def test_a_flag_is_reported_as_a_boolean(self, public: APIClient) -> None:
        """A client branches on these. A flag arriving as the string "False"
        is truthy in JavaScript, which turns a dark feature on."""
        for value in _body(public)["features"].values():
            assert isinstance(value, bool)


class TestNothingOutsideTheAllowListEscapes:
    """The reason this module exists."""

    def test_the_payload_holds_exactly_the_allow_listed_names(self, public: APIClient) -> None:
        assert set(_body(public)) == set(PUBLIC_SETTINGS) | {"features"}

    def test_a_newly_registered_setting_is_private_by_default(
        self, public: APIClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure this whole file guards against.

        Somebody adds a row — here a dispatch weight, which is the §11.6 case
        a provider could actually exploit — and it must not appear merely
        because it now exists. Being served requires an edit to
        `PUBLIC_SETTINGS`, which is a diff a reviewer sees.
        """
        secret = config_module.Setting("dispatch.w_rate_probe", "0.25", "probe")
        monkeypatch.setitem(SETTINGS_REGISTER, secret.key, secret)

        body = _body(public)
        assert "dispatch.w_rate_probe" not in body
        assert "w_rate_probe" not in body.get("features", {})
        assert "0.25" not in str(body)

    def test_a_new_feature_flag_is_served_without_a_code_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half. §35 ships capabilities dark, so adding a flag must
        not require editing the allow-list — otherwise the mechanism acquires
        ceremony and stops being used."""
        flag = config_module.Setting(f"{FEATURE_FLAG_PREFIX}probe_capability", True, "probe")
        monkeypatch.setitem(SETTINGS_REGISTER, flag.key, flag)
        assert public_config()["features"]["probe_capability"] is True

    def test_every_allow_listed_key_is_actually_registered(self) -> None:
        """A typo, or a key deleted from the register, would otherwise surface
        as an `UnknownSettingError` on the most-called route on the platform —
        and only in production, because nothing else reads these."""
        missing = {key for key in PUBLIC_SETTINGS.values() if key not in SETTINGS_REGISTER}
        assert not missing, f"allow-list names unregistered settings: {sorted(missing)}"

    def test_no_flag_carries_a_business_value(self) -> None:
        """`feature.*` is public *by construction*, so the prefix has to mean
        "client-visible switch" and nothing else. A threshold or a rate named
        `feature.…` would be published without anyone deciding to publish it.
        """
        for key in feature_flag_keys():
            default = SETTINGS_REGISTER[key].default
            assert isinstance(default, bool), (
                f"{key} defaults to {default!r}. Only booleans may live under "
                f"{FEATURE_FLAG_PREFIX!r}; anything else is a business value "
                "and would be served publicly by the prefix rule."
            )

    def test_the_register_is_far_larger_than_what_is_served(self) -> None:
        """Guards the guard: if the allow-list ever grew to most of the
        register, the tests above would still pass while the endpoint had
        quietly become the thing this design exists to prevent."""
        served = len(PUBLIC_SETTINGS) + len(feature_flag_keys())
        assert served * 4 < len(SETTINGS_REGISTER), (
            f"{served} of {len(SETTINGS_REGISTER)} settings are public. That is a "
            "large enough share to be worth re-reading public_config.py's premise."
        )
