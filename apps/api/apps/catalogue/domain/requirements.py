"""Activity requirements — SRS §16.4.

    requirements JSONB carries structured, machine-checkable restrictions that
    feed validation rule VR-15 and the booking guards:

    { "min_age": 8, "max_age": null, "swimming_ability_required": true,
      "medical_declarations": ["pregnancy", "heart_condition"],
      "what_to_bring": ["swimwear", "towel", "sunscreen"],
      "not_suitable_for": ["reduced_mobility"] }

    Free-text is rendered to the tourist; structured keys are validated against
    the trip party where the data exists.

Two of those keys are safety controls. `min_age` and `swimming_ability_required`
are what stop a six-year-old being sold a deep-water snorkelling trip, and
VR-15 and the Phase 5 booking guards read them by name. That is why **unknown
keys are rejected** rather than carried through: a provider or administrator
typing `minimum_age` instead of `min_age` would otherwise create a listing that
looks correctly restricted in the console and enforces nothing at booking, and
nothing in the system would report a problem until somebody got hurt.

The same reasoning makes this validation belong in Phase 3 rather than in
Phase 5 with the guards that consume it. Listings are created here, through the
§27.8 console. A malformed restriction rejected at the form is a typo; the same
restriction discovered at booking time is a listing that has been live and
unenforced for two months.

Free-text lists (`what_to_bring`, `medical_declarations`, `not_suitable_for`)
are validated for *shape* only — they are rendered, not matched — because §16.1
is emphatic that no activity vocabulary appears in application code. A market
that needs "altitude_sickness" adds it as data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["ActivityRequirements", "RequirementsError", "parse_requirements"]

_KNOWN_KEYS = frozenset(
    {
        "min_age",
        "max_age",
        "swimming_ability_required",
        "medical_declarations",
        "what_to_bring",
        "not_suitable_for",
    }
)

_TEXT_LIST_KEYS = ("medical_declarations", "what_to_bring", "not_suitable_for")

#: Nobody is 150. A ceiling makes a transposed year of birth a validation error
#: rather than a restriction that silently matches every traveller.
_MAX_PLAUSIBLE_AGE = 120


class RequirementsError(ValueError):
    """The stored JSON does not match the §16.4 schema."""


@dataclass(frozen=True, slots=True)
class ActivityRequirements:
    """The §16.4 structure, typed.

    `min_age`/`max_age` are `None` when unrestricted, which is different from
    zero: a `min_age` of 0 is a provider explicitly stating infants are
    welcome, and collapsing the two would lose that.
    """

    min_age: int | None = None
    max_age: int | None = None
    swimming_ability_required: bool = False
    medical_declarations: tuple[str, ...] = ()
    what_to_bring: tuple[str, ...] = ()
    not_suitable_for: tuple[str, ...] = ()

    @property
    def is_unrestricted(self) -> bool:
        """No machine-checkable restriction. Free-text does not count."""
        return self.min_age is None and self.max_age is None and not self.swimming_ability_required

    def admits_age(self, age: int) -> bool:
        """Does a traveller of `age` satisfy the age bounds? VR-15.

        Bounds are inclusive: `min_age: 8` admits an eight-year-old, which is
        how a provider writing "8+" means it to be read.
        """
        if age < 0:
            raise ValueError("age cannot be negative")
        if self.min_age is not None and age < self.min_age:
            return False
        return not (self.max_age is not None and age > self.max_age)


def parse_requirements(raw: Mapping[str, object] | None) -> ActivityRequirements:
    """Validate and structure the stored JSONB. Called on every admin write."""
    if not raw:
        return ActivityRequirements()

    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        # A typo here is a safety control that silently enforces nothing.
        raise RequirementsError(
            f"unknown requirement keys: {sorted(unknown)}; known keys are {sorted(_KNOWN_KEYS)}"
        )

    min_age = _parse_age(raw.get("min_age"), where="min_age")
    max_age = _parse_age(raw.get("max_age"), where="max_age")
    if min_age is not None and max_age is not None and max_age < min_age:
        raise RequirementsError(f"max_age {max_age} is below min_age {min_age}")

    swimming = raw.get("swimming_ability_required", False)
    if not isinstance(swimming, bool):
        raise RequirementsError("swimming_ability_required must be a boolean")

    lists = {key: _parse_text_list(raw.get(key, ()), where=key) for key in _TEXT_LIST_KEYS}

    return ActivityRequirements(
        min_age=min_age,
        max_age=max_age,
        swimming_ability_required=swimming,
        **lists,
    )


def _parse_age(value: object, *, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        # bool is an int in Python, and `min_age: true` is not an age.
        raise RequirementsError(f"{where} must be a whole number or null")
    if value < 0:
        raise RequirementsError(f"{where} cannot be negative")
    if value > _MAX_PLAUSIBLE_AGE:
        raise RequirementsError(f"{where} of {value} is implausible")
    return value


def _parse_text_list(value: object, *, where: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise RequirementsError(f"{where} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise RequirementsError(f"{where} must contain only strings")
        trimmed = item.strip()
        if not trimmed:
            raise RequirementsError(f"{where} cannot contain an empty entry")
        if trimmed not in items:
            items.append(trimmed)
    return tuple(items)
