# ADR 0020 — A stay anchors every night it covers, not only its first and last

**Status:** Accepted · **Date:** 2026-08-31 · **Phase:** 4

## Context

§10.4's day-sequencing algorithm works one day at a time. Line 3 gathers the
day's fixed items and line 4 lists what those are:

> `fixed := items on day with a provider-imposed time (ACTIVITY with
> departure, flight anchors, check-in/out)`

A stay therefore appears twice in a trip: as a **check-in** on the day it
begins and a **check-out** on the day it ends. On every day between, the
accommodation is not in the list at all.

Line 11 then inserts a transfer only where two *adjacent* items are in
different places. A middle day holding a single activity has nothing adjacent
to it, so no transfer is planned to reach it.

**The consequence, on an ordinary trip.** Four nights, a hotel, and a kayak
tour across town on day two:

| | |
|---|---|
| Day 1 | check-in (14:00) + activity → transfer planned |
| **Day 2** | **activity only → no transfer at all** |
| Day 4 | check-out (10:00) + flight → transfer planned |

Day 2 is the shape of most days of most trips. The tourist is shown an
activity with no way of getting to it, on a screen whose entire purpose is to
answer that question. §3.1 calls the platform's job *"orchestration of the
journey, not a list of things to buy"*, and a plan that omits the journey on
the days between arrival and departure is the list.

This was found by fixing an unrelated defect. A timezone bug had been
collapsing two different local days into one, so a test asserting "a transfer
appears between a stay and an activity" passed while the days were wrong.
Correcting the zone made the transfer disappear and the gap visible.

## Decision

**A stay is present on every day it covers.** In addition to the check-in and
check-out anchors §10.4 already names, each intervening day gains a
**departure anchor** at the start of that day, in the destination's timezone,
carrying the stay's location.

The sequencer then sees `hotel → activity` on a middle day exactly as it sees
`check-in → activity` on the first, and line 13 inserts the transfer by the
same rule. No new rule is added; the existing one is given the input it was
always missing.

**The anchor's rank is `STAY check-out` (1).** §10.4's tie-break list is
unchanged. Rank 1 means "leaving the accommodation", which is precisely what a
morning departure is, and it sorts early — before the day's activities, which
is where it belongs.

**The anchor's time is local midnight, and that is not a claim about when
anybody gets up.** It exists to order the day, and the transfer's real times
are derived from the item it serves: §10.4 line 14 times a leg backwards from
`B.starts_at` less the buffer. So the tourist is told when to leave in order to
arrive, which is a computed fact, not an invented start to the day.

### What this does not do

**No return leg is planned.** A transfer back to the accommodation would have
to be timed from an end-of-day anchor, and nothing knows when a day ends. An
anchor at local midnight would produce a plan that has the tourist travelling
at 23:30, which is worse than no plan at all — so the evening journey stays the
tourist's own, and this is stated here rather than discovered on screen.

## Consequences

**This is a change to §10.4 and is recorded as one.** §10.1 requires that "two
implementations produce identical output", which is only meaningful if the
algorithm they implement is written down. An implementation that quietly
inserted an anchor the specification does not describe would break that promise
in the least visible way — the code would be right and the document wrong, and
the next reader would trust the wrong one. §10.4's line 4 should be amended to
read *"ACTIVITY with departure, flight anchors, check-in/out, and a departure
anchor for each intervening night of a stay"*.

**Determinism is preserved.** The anchor is derived from the stay's own dates
and the destination's zone, both already inputs, so the same trip still
produces the same plan. TC-902 and TC-043 cover it.

**More transfers means more estimates.** Until Appendix D-2 is decided every
one of them is a haversine figure labelled `APPROXIMATE` (ADR 0019), so this
increases the number of approximate durations on screen. That is an argument
for choosing a routing provider, not against planning the journey — a labelled
estimate a tourist can act on beats an empty day they cannot.

**A stay with no location cannot anchor anything.** ADR 0013 allows a
free-entry stay whose pin the tourist never confirmed; §13.2 forbids persisting
an unconfirmed geocode, so such a stay has no coordinate and produces no
transfer. That is the existing behaviour and remains correct: the alternative
is routing to a place nobody confirmed.
