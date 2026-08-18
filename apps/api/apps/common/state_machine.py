"""Table-driven state machine core.

SRS §36.2: "holds the single table-driven transition validator used by every
machine, so that no module implements its own."

SRS principle A4: "Booking, payment, trip and assignment status transitions
are declared in one table per machine and validated centrally."

Pure — no Django, no ORM, no I/O. Layer 3 (SRS §8.2). A machine is data: a
set of states, a set of allowed transitions, and optional guards. Adding a
transition is a one-line table edit, and the table is readable by a
non-engineer reviewing whether the booking lifecycle is correct.

Guards are pure predicates over a caller-supplied context. They express
business conditions ("policy permits cancellation"); they must not perform
I/O. Resolve whatever a guard needs *before* calling `transition`.

The seven machines of SRS Appendix A are declared by their owning modules,
not here — `common` owns the mechanism, not the policy.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from apps.common.errors import ConflictError

__all__ = ["Transition", "StateMachine", "IllegalTransitionError", "GuardFailedError"]

S = TypeVar("S")


class IllegalTransitionError(ConflictError):
    """The requested transition is not declared in the machine's table."""

    code = "ILLEGAL_TRANSITION"

    def __init__(self, machine: str, source: Any, target: Any) -> None:
        super().__init__(f"{machine}: cannot move from {source} to {target}.")
        self.machine = machine
        self.source = source
        self.target = target


class GuardFailedError(ConflictError):
    """The transition is declared but a guard rejected it."""

    code = "TRANSITION_GUARD_FAILED"

    def __init__(self, machine: str, source: Any, target: Any, guard: str) -> None:
        super().__init__(f"{machine}: {source} to {target} is blocked by {guard}.")
        self.machine = machine
        self.source = source
        self.target = target
        self.guard = guard


@dataclass(frozen=True, slots=True)
class Transition(Generic[S]):
    """One declared edge of a machine."""

    source: S
    target: S
    guard: Callable[[Mapping[str, Any]], bool] | None = None
    guard_name: str = ""

    def __post_init__(self) -> None:
        if self.guard is not None and not self.guard_name:
            name = getattr(self.guard, "__name__", "guard")
            object.__setattr__(self, "guard_name", name)


@dataclass(frozen=True, slots=True)
class StateMachine(Generic[S]):
    """A declared machine.

    Example — the Trip machine of SRS §20.5::

        TRIP_MACHINE = StateMachine(
            name="trip",
            initial=TripStatus.DRAFT,
            terminal={TripStatus.COMPLETED, TripStatus.CANCELLED},
            transitions=[
                Transition(TripStatus.DRAFT, TripStatus.PRICED),
                Transition(TripStatus.PRICED, TripStatus.PENDING_PAYMENT),
                ...
            ],
        )
    """

    name: str
    initial: S
    transitions: Iterable[Transition[S]]
    terminal: frozenset[S] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "transitions", tuple(self.transitions))
        object.__setattr__(self, "terminal", frozenset(self.terminal))

        index: dict[tuple[S, S], Transition[S]] = {}
        for t in self.transitions:
            key = (t.source, t.target)
            if key in index:
                raise ValueError(
                    f"{self.name}: duplicate transition {t.source} to {t.target}. "
                    "Express alternatives as one transition with a guard."
                )
            if t.source in self.terminal:
                raise ValueError(
                    f"{self.name}: {t.source} is terminal but has an outgoing transition."
                )
            index[key] = t
        object.__setattr__(self, "_index", index)

    @property
    def states(self) -> frozenset[S]:
        out: set[S] = {self.initial, *self.terminal}
        for t in self.transitions:  # type: ignore[attr-defined]
            out.add(t.source)
            out.add(t.target)
        return frozenset(out)

    def allowed_targets(self, source: S) -> frozenset[S]:
        """Every state reachable from `source` in one step, ignoring guards."""
        idx: dict[tuple[S, S], Transition[S]] = self._index  # type: ignore[attr-defined]
        return frozenset(target for (src, target) in idx if src == source)

    def can(self, source: S, target: S, context: Mapping[str, Any] | None = None) -> bool:
        """True if the transition is declared and its guard passes."""
        idx: dict[tuple[S, S], Transition[S]] = self._index  # type: ignore[attr-defined]
        t = idx.get((source, target))
        if t is None:
            return False
        if t.guard is None:
            return True
        return bool(t.guard(context or {}))

    def transition(self, source: S, target: S, context: Mapping[str, Any] | None = None) -> S:
        """Validate and return the new state.

        Raises `IllegalTransitionError` (SRS §32.3 `ILLEGAL_TRANSITION`, 409)
        if the edge is not declared, or `GuardFailedError` if a guard rejects
        it. Returns the target so callers can assign the result directly.
        """
        idx: dict[tuple[S, S], Transition[S]] = self._index  # type: ignore[attr-defined]
        t = idx.get((source, target))
        if t is None:
            raise IllegalTransitionError(self.name, source, target)
        if t.guard is not None and not t.guard(context or {}):
            raise GuardFailedError(self.name, source, target, t.guard_name)
        return target

    def is_terminal(self, state: S) -> bool:
        return state in self.terminal
