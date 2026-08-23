# ADR 0015 — Ratings are a projection into `catalogue`, and a mean below the display threshold is not served

**Status:** Accepted · **Date:** 2026-08-23 · **Phase:** 3 · **Resolves:** Q4

## Context

SRS §16.5 fixes the default catalogue ordering, and the fourth and fifth terms
are ratings:

```
ORDER BY
  (a.destination_id = :selected_destination) DESC,
  (a.tags && :interest_tags) DESC,
  a.feature_rank ASC,
  agg.rating_avg DESC NULLS LAST,        -- published reviews
  agg.rating_count DESC,
  a.price_per_person ASC,
  a.id ASC
```

`agg` is `rating_aggregate`, which SRS §6.4 gives to `review`. `review` sits at
L5 and depends on `booking`; `catalogue` is L1 and depends on `location`.
Neither may import the other, and §6.5 rule 3 forbids the reading module
traversing a relation into the other module's columns even though the foreign
key would be permitted in a single database. **So the join in §16.5 cannot exist
as written.** The ordering is not optional — §16.5 is a published commitment,
quoted in the help centre *"so that providers understand exactly how placement
is earned"* — so it has to be satisfiable without the join.

This is the phase's last unresolved question (Q4), and it stopped being
theoretical at commit 26: the public read API ships §16.5 as a seven-term
keyset-paginated ordering, and `rating_avg` and `rating_count` are two of the
seven terms carried in the cursor.

## Decision

### 1. The aggregate is denormalised onto the ranked table

`activity` carries `rating_avg NUMERIC(3,2) NOT NULL DEFAULT 0.00` and
`rating_count INTEGER NOT NULL DEFAULT 0`. `review` remains the owner of the
truth; these two columns are its **projection**, and no other module writes
them.

This is what SRS §7.5 already specifies — its column table gives
`rating_avg | NUMERIC(3,2) | N | 0.00 | Denormalised from rating_aggregate`.
The Phase 3 plan's Q4 note recommended `NUMERIC(2,1) NULL`; **that
recommendation was wrong against the SRS table and is not adopted.** The shipped
schema follows §7.5. `NULLS LAST` survives in the domain's `OrderTerm` because
`rank_key` also describes the join-shaped expression, and because a term that
declares its null handling explicitly cannot acquire PostgreSQL's default
`NULLS FIRST` for `DESC` by accident — which is the wrong one.

`accommodation` carries neither column. ADR 0013 removed the pair when
accommodation stopped being a bookable product: a curated location record has
nothing to aggregate, and a rating on it would be a claim about a property the
platform does not sell.

### 2. `review` publishes; `catalogue` subscribes; neither imports the other

`review` publishes `RatingAggregateRecomputed` on the existing
`apps.common.events` bus, carrying the subject type, the subject's `public_id`,
the new mean and the new count — primitives and identifiers only, per
`DomainEvent`'s contract, because a handler runs in a different transaction and
an ORM instance would be stale by the time it is read. A `catalogue`-side
subscriber applies it to the projection.

The bus is in the shared kernel, which both modules already depend on. This is
the same resolution used for settings (ADR 0003), audit (Phase 2) and
authorisation (ADR 0005), and it is what §6.5 rule 4 prescribes for exactly this
situation: *"Modules that need to react to another module's state changes
subscribe to domain events rather than calling back synchronously."*

**The projection is eventually consistent, and that is acceptable here.** A
ranking a few seconds stale returns a slightly different order; nothing is
mispriced, no availability is oversold, and no money moves. That property is
what makes a projection safe here and would not make one safe in `inventory`.

### 3. A mean below the display threshold is not served at all

BR-127:

> Ratings are aggregated in `rating_aggregate` per subject with count, mean to
> two decimals, and a per-star histogram; **a subject with fewer than 3
> published reviews displays "New" rather than a mean**.

This was implemented nowhere. The read API served `rating_avg` unconditionally,
so every client was free to render `5.0 ★` for an activity with one review —
and four screens are about to be built against that payload.

**The rule is applied on the server, not delegated to clients.** `rating_avg` is
`null` in the payload whenever `rating_count` is below the threshold, and
`rating_count` is served alongside it so a client can render "New" without a
second call. A client cannot violate BR-127 because it is never handed the
number it would violate it with.

The alternative — serving the mean and documenting the rule — puts one business
rule in four implementations, of which some number greater than zero will be
wrong. The failure mode is a public page overstating a provider's quality on the
strength of a single review, which is a commercial claim the platform cannot
support.

**The threshold is `review.min_display_count`, a `system_setting` row**,
defaulting to 3. Per rule 5 no business threshold is a literal in code, and this
one is a judgement about statistical confidence that a market with thinner
supply may reasonably want to move.

## The tension this leaves, stated rather than hidden

BR-127 governs **display**. §16.5 governs **ranking**, and it ranks on the true
`rating_avg` before `rating_count` breaks the tie. So an activity with a single
five-star review outranks one with fifty reviews averaging 4.8 — and, under the
decision above, shows "New" while doing it.

That is a placement a provider could manufacture with one review, and it is
visible precisely because the ordering is published. It is **not** resolved
here, because §16.5 is a specified, published ordering and changing it is a
Product Owner decision rather than an implementation one. The options, for that
conversation:

* order on a confidence-weighted score rather than a raw mean — still
  deterministic and still explicable, but no longer the expression §16.5
  publishes;
* rank subjects below the display threshold as though unrated, so ranking and
  display agree — the smallest change, at the cost of a published expression
  that is slightly harder to state;
* accept it, on the grounds that review moderation and the §30.14 fraud rules
  are where manufactured reviews are supposed to be caught.

Phase 12 owns `review` and is where this has to be settled. Recording it now
means it is decided by someone who can see it, rather than discovered from a
provider complaint.

## Consequences

**Phase 3 ships the columns, the ordering and the display rule; the subscriber
is Phase 12's.** There are no reviews yet, so every `rating_avg` is `0.00` and
every `rating_count` is `0` — which means every activity is below the threshold
and the API serves `rating_avg: null` for all of them. That is the correct
launch-day behaviour, not a degenerate case: on day one nothing has been
reviewed and nothing should claim to have been.

**Ordering tests set the columns directly.** Not a shortcut — it is the only way
to test the §16.5 ordering in isolation from a module that does not exist yet,
and it keeps `tests/test_selectors_ranking_db.py` a test of the ordering rather
than of `review`.

**The columns are not administrator-writable.** They are absent from
`repositories._WRITABLE`, so the §27.8 console cannot set them and — since
`snapshot` derives from the same set — they stay out of the §41.13 audit diff. A
projection an administrator can edit is not a projection.

**A `rating_avg` of `0.00` is a sentinel, and the schema permits a real one.**
The CHECK is `0 <= rating_avg <= 5`, so "unrated" and "rated zero" are the same
stored value. Nothing distinguishes them except `rating_count`, which is why
`rating_count` and not `rating_avg` is what the display rule tests. Any future
code asking "has this been rated" must ask `rating_count`; asking
`rating_avg > 0` is a bug that will look like it works.
