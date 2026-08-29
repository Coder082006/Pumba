"""The port registry, and the two ports deliberately not in it.

SRS §8.4 puts every external dependency behind a port so it can be swapped
without touching business code. Seven of the nine declared ports resolve to a
fake when no adapter is configured. Two do not, and **that is the interesting
part of this file**: `routing` and `payment` have no entry in `_FAKES` and no
accessor, so nothing can reach them at all.

Today that is an accident that happens to be correct — nobody added them
because nothing needed them yet. An accident that happens to be correct is
one refactor away from being wrong, and the failure would be quiet:
`FakeRouting.geocode` derives a coordinate from a **sha256 of the query
string**. It returns a plausible-looking point in Zanzibar for any input. A
tourist confirming a map pin over that number would be vouching for a hash,
which is exactly what §13.2 forbids and precisely the fabrication this project
already declined once, on the distance chip.

So the absence is asserted, with its reason attached to the open decision that
governs it:

* **`routing` — Appendix D, D2.** Blocks Phase 4's itinerary sequencing, which
  needs real travel times. Until a provider is chosen, no code path may reach
  a fabricated coordinate or a fabricated duration.
* **`payment` — Appendix D, D1.** A fake gateway that "succeeds" is the single
  most dangerous test double in this system, and §21 has real money on the
  other side of it.

The pattern is the one `test_the_public_set_is_exactly_this` uses for
`public_config`: state the set, and make widening it a decision somebody makes
in a file a reviewer reads rather than a line that slips through.
"""

from __future__ import annotations

import pytest

from apps.common import ports_registry

#: Ports with no adapter, no fake and no accessor — and the decision that
#: keeps them that way. Adding one here is not enough on its own: the tests
#: below re-prove that each is genuinely unreachable.
DELIBERATELY_UNREGISTERED = {
    "routing": "Appendix D, D2. FakeRouting.geocode returns a sha256-derived "
    "coordinate; §13.2 forbids persisting an unconfirmed geocode, and a "
    "confirmation over a hash is not a confirmation.",
    "payment": "Appendix D, D1. A fake gateway that reports success is the "
    "most dangerous double in the system; §21 has real money behind it.",
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
        }

    def test_no_deliberately_unregistered_port_has_a_fake(self) -> None:
        for name in DELIBERATELY_UNREGISTERED:
            assert name not in ports_registry._FAKES, (
                f"{name} has acquired a fake. {DELIBERATELY_UNREGISTERED[name]} "
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

    @pytest.mark.parametrize("name", sorted(DELIBERATELY_UNREGISTERED))
    def test_each_absence_states_its_reason(self, name: str) -> None:
        """A list of names with no reasons decays into a list nobody trusts."""
        reason = DELIBERATELY_UNREGISTERED[name]
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
        exported = {name for name in ports_registry.__all__ if name.startswith("get_")}
        assert len(exported) == len(ports_registry._FAKES)
