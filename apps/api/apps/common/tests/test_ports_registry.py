"""The port registry, and the ports deliberately not defaulted in it.

SRS §8.4 puts every external dependency behind a port so it can be swapped
without touching business code. Most ports resolve to a fake when no adapter is
configured. Two do not, and **that is the interesting part of this file**.

`FakeRouting.geocode` derives a coordinate from a **sha256 of the query
string**. It returns a plausible-looking point in Zanzibar for any input. A
tourist confirming a map pin over that number would be vouching for a hash,
which is exactly what §13.2 forbids and precisely the fabrication this project
already declined once, on the distance chip. And a fake gateway that reports a
payment succeeded is the same failure with money behind it.

So each absence is asserted, with its reason attached to the open decision that
governs it. **The two are no longer the same absence**, and ADR 0027 is where
that changed:

* **`routing` — Appendix D, D2. Unreachable.** No fake, no accessor, nothing
  can ask for it at all. Until a provider is chosen, no code path may reach a
  fabricated coordinate or a fabricated duration.
* **`payment` — Appendix D, D1. Reachable, never by default.** Phase 8 has to
  take a payment, so `get_payment_port()` exists — but `payment` has no entry
  in `_FAKES`, and an unconfigured port raises `AdapterNotConfiguredError`
  instead of resolving one. The fake is reachable by being *named* in
  `PORT_ADAPTERS`, which `config.settings.ci` does and no production settings
  module may. What was being protected was never the accessor; it was that
  nobody inherits a gateway they did not choose.

The pattern is the one `test_the_public_set_is_exactly_this` uses for
`public_config`: state the set, and make widening it a decision somebody makes
in a file a reviewer reads rather than a line that slips through.
"""

from __future__ import annotations

import pytest

from apps.common import ports_registry
from ports.fakes import FakePaymentGateway

#: Ports with no adapter, no fake and no accessor — and the decision that
#: keeps them that way. Adding one here is not enough on its own: the tests
#: below re-prove that each is genuinely unreachable.
DELIBERATELY_UNREGISTERED = {
    "routing": "Appendix D, D2. FakeRouting.geocode returns a sha256-derived "
    "coordinate; §13.2 forbids persisting an unconfirmed geocode, and a "
    "confirmation over a hash is not a confirmation.",
}

#: Ports that may be asked for but have no default: an unconfigured one raises
#: rather than resolving a fake. ADR 0027.
NO_DEFAULT = {
    "payment": "Appendix D, D1. A gateway that reports success without one "
    "being configured is the most dangerous double in the system; §21 has real "
    "money behind it. Naming the fake is allowed; inheriting it is not.",
}


class TestTheRegisteredSet:
    def test_the_fakes_are_exactly_these(self) -> None:
        """A new fake appearing here is a deliberate widening.

        Stated as an exact set rather than a subset check: the risk is a port
        gaining a fake nobody meant it to have, and a subset assertion cannot
        see that.
        """
        assert set(ports_registry._FAKES) == {
            "email",
            "sms",
            "push",
            "crypto",
            "breach",
            "storage",
            "exchange_rate",
            # ADR 0026: voucher rendering. No external call, no money.
            "document",
            # `payment` is deliberately absent even though `get_payment_port`
            # exists (ADR 0027): an accessor with no default is the whole point.
        }

    def test_no_deliberately_unregistered_port_has_a_fake(self) -> None:
        for name in {**DELIBERATELY_UNREGISTERED, **NO_DEFAULT}:
            reason = {**DELIBERATELY_UNREGISTERED, **NO_DEFAULT}[name]
            assert name not in ports_registry._FAKES, (
                f"{name} has acquired a fake. {reason} "
                "If the decision has changed, change it here first."
            )

    def test_no_accessor_exists_for_them(self) -> None:
        """`get_routing_port` must not exist.

        The fake is importable — `ports.fakes.FakeRouting` is real, tested,
        and would work. What stops it reaching production code is that there
        is no supported way to ask for it, so anyone who wants one has to add
        an accessor, which is a diff a reviewer sees.
        """
        for name in DELIBERATELY_UNREGISTERED:
            accessor = f"get_{name}_port"
            assert not hasattr(
                ports_registry, accessor
            ), f"{accessor} now exists. {DELIBERATELY_UNREGISTERED[name]}"
            assert accessor not in ports_registry.__all__

    def test_resolving_one_raises_rather_than_falling_back(self) -> None:
        """The failure mode that matters.

        `_resolve` reads `_FAKES[name]` for an unconfigured port. If it ever
        gained a `.get(name)` with a default, an unregistered port would
        silently resolve to `None` or to something plausible instead of
        failing — and a routing port that quietly returns nothing is a trip
        planner that quietly plans nothing.
        """
        for name in DELIBERATELY_UNREGISTERED:
            with pytest.raises(KeyError):
                ports_registry._resolve(name)

    def test_a_port_with_no_default_refuses_to_resolve_one(self, settings) -> None:  # type: ignore[no-untyped-def]
        """ADR 0027, and the reason the accessor is safe to have.

        The CI settings name the fake, so this asks what a deployment that has
        not configured a gateway gets: an error that says so, rather than a
        double that reports every payment captured.
        """
        for name in NO_DEFAULT:
            settings.PORT_ADAPTERS = {
                key: value for key, value in settings.PORT_ADAPTERS.items() if key != name
            }
            ports_registry.reset_ports()
            with pytest.raises(ports_registry.AdapterNotConfiguredError, match=name):
                ports_registry._resolve(name)
        ports_registry.reset_ports()

    def test_naming_the_fake_is_the_one_way_to_reach_it(self, settings) -> None:  # type: ignore[no-untyped-def]
        """What `config.settings.ci` does, asserted rather than assumed."""
        settings.PORT_ADAPTERS = {
            **settings.PORT_ADAPTERS,
            "payment": "ports.fakes.FakePaymentGateway",
        }
        ports_registry.reset_ports()

        assert isinstance(ports_registry.get_payment_port(), FakePaymentGateway)
        ports_registry.reset_ports()

    @pytest.mark.parametrize("name", sorted({**DELIBERATELY_UNREGISTERED, **NO_DEFAULT}))
    def test_each_absence_states_its_reason(self, name: str) -> None:
        """A list of names with no reasons decays into a list nobody trusts."""
        reason = {**DELIBERATELY_UNREGISTERED, **NO_DEFAULT}[name]
        assert len(reason) > 40
        assert "Appendix D" in reason, "name the open decision that governs it"


class TestEveryAccessorResolves:
    """The other half: what *is* registered must actually work.

    Asserting only the absences would leave the registry free to be broken in
    the ordinary direction.
    """

    @pytest.mark.parametrize(
        "accessor",
        [
            "get_email_port",
            "get_sms_port",
            "get_push_port",
            "get_crypto_port",
            "get_breach_port",
            "get_storage_port",
            "get_exchange_rate_port",
        ],
    )
    def test_it_returns_an_adapter(self, accessor: str) -> None:
        ports_registry.reset_ports()
        assert getattr(ports_registry, accessor)() is not None

    def test_every_accessor_is_exported(self) -> None:
        """One accessor per fake, plus one per port that has no default.

        The arithmetic is the guard: an accessor for a port that is neither
        faked nor declared here would make the two sides disagree.
        """
        exported = {name for name in ports_registry.__all__ if name.startswith("get_")}
        assert len(exported) == len(ports_registry._FAKES) + len(NO_DEFAULT)
        for name in NO_DEFAULT:
            assert f"get_{name}_port" in exported
