"""Public visibility — the catalogue's analogue of the ownership predicate.

SRS §4.1 and §41.12 between them require three independent reasons a catalogue
row may exist and still not be public:

* ``is_active`` — Pemba is seeded ``is_active = false`` and must not appear
  anywhere public (§4.1). Administrators toggle this at will.
* ``launch_date`` — *"Enables scheduled market launch without a deployment"*
  (§4.1). A destination whose launch date has not arrived is invisible today
  and visible tomorrow, with nobody deploying anything.
* ``deleted_at`` — soft deletion (§7.7). A deleted row is gone from the public
  surface but retained for referential integrity.

And a fourth reason that is not the row's own: **its parent**. An attraction
inside a deactivated destination is invisible whatever its own flag says, and a
destination inside a deactivated region likewise. Without that, deactivating
Pemba would hide the destination and leave its attractions reachable by direct
URL and listed in the sitemap.

Since ADR 0018 the chain is one level deeper — ``market`` sits between country
and region and carries its own ``launch_date`` — and there is one weaker
predicate beside the rule, ``is_listed``, for the single screen that has to
show a market it is not yet serving. See its docstring for why it takes no
``launch_date`` rather than ignoring one.

Those four are one rule, not four checks. Keeping them one rule is the whole
point of this module: three flags and a parent chain evaluated in four
different places will drift, and the drift is silent — a row that should be
hidden appearing on one endpoint out of nine is not something anybody notices
until it is a customer complaint.

`selectors.py` compiles the same rule into a queryset filter, because a row
that is never loaded cannot leak through a serializer. `tests/test_domain_
visibility.py` asserts the pure function and the compiled filter agree over the
whole truth table rather than trusting that they do.

`today` is a parameter. The domain never reads the clock, and "today" is the
destination's today, not the server's — see `opening_hours` for why that
distinction has teeth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

__all__ = [
    "VisibilityNode",
    "is_listed",
    "is_publicly_visible",
    "visible_chain",
    "hidden_reason",
]


@dataclass(frozen=True, slots=True)
class VisibilityNode:
    """One link in a country → region → destination → listing chain.

    `launch_date` is `None` for everything except `destination`, which is the
    only entity §4.1 gives a scheduled launch. Modelling it on the node rather
    than on the destination alone means the same predicate serves the whole
    chain, and a future entity gaining a launch date needs no new code.
    """

    is_active: bool
    deleted_at: datetime | None = None
    launch_date: date | None = None


def is_listed(*, is_active: bool, deleted_at: datetime | None) -> bool:
    """Does this row exist as far as the public is concerned?

    Weaker than `is_publicly_visible` by exactly one term: it does not consult
    `launch_date`. **It takes no `launch_date` and no `today`, so it cannot
    consult one** — a version that accepted the argument and ignored it would
    be one refactor away from reading it.

    This is the predicate ADR 0018 gives the destination selector, and the
    market tier is the only caller. An announced market has to appear on the
    landing page saying it is not open yet, which is precisely a row that is
    *listed* and not *visible*. Nothing else in the catalogue has that state,
    and nothing else may use this: an attraction that is listed but not
    visible is a leak.
    """
    return deleted_at is None and is_active


def is_publicly_visible(
    *,
    is_active: bool,
    deleted_at: datetime | None,
    launch_date: date | None,
    today: date,
) -> bool:
    """Is this row itself public, ignoring its parents?

    A `launch_date` of exactly `today` **is** visible: §4.1 calls it a launch
    date, and a market that launches on the 12th is open on the 12th.

    Written as a *narrowing of* `is_listed` rather than as its own three
    checks. That is what makes the pair safe: the two predicates cannot drift
    apart on activation or deletion, because there is one implementation of
    those, and the single cell where they differ — active, undeleted, not yet
    launched — is the only thing this function adds. ADR 0018's whole design
    rests on that difference being exactly one cell wide, and this is the
    cheapest way to make it true by construction instead of by test.
    """
    if not is_listed(is_active=is_active, deleted_at=deleted_at):
        return False
    return not (launch_date is not None and launch_date > today)


def visible_chain(*nodes: VisibilityNode, today: date) -> bool:
    """Is the last node public, given every ancestor passed before it?

    Empty chain is `False`. A listing with no destination is not a listing with
    nothing to hide it — it is a bug, and returning `True` would publish it.
    """
    if not nodes:
        return False
    return all(
        is_publicly_visible(
            is_active=node.is_active,
            deleted_at=node.deleted_at,
            launch_date=node.launch_date,
            today=today,
        )
        for node in nodes
    )


def hidden_reason(*nodes: VisibilityNode, today: date) -> str | None:
    """Why is this hidden? `None` when it is not.

    For the administration console, which must show an administrator *why* a
    row they can see is not on the public site. Returning the reason for the
    outermost failing ancestor first is deliberate: "the region is inactive" is
    the actionable answer, and "the attraction is inactive" would send them to
    re-toggle a flag that was never the problem.
    """
    if not nodes:
        return "NO_PARENT"
    for depth, node in enumerate(nodes):
        if node.deleted_at is not None:
            return f"DELETED@{depth}"
        if not node.is_active:
            return f"INACTIVE@{depth}"
        if node.launch_date is not None and node.launch_date > today:
            return f"NOT_YET_LAUNCHED@{depth}"
    return None
