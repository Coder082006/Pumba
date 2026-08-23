# ADR 0017 — Ranking uses the displayable rating, not a confidence-weighted mean

**Status:** Accepted · **Date:** 2026-08-23 · **Phase:** 3 · **Resolves:** the open question in [ADR 0015](0015-ratings-are-a-projection-into-catalogue.md)

## Context

ADR 0015 recorded a tension and deliberately left it open:

> BR-127 governs **display**. §16.5 governs **ranking**, and it ranks on the
> true `rating_avg` before `rating_count` breaks the tie. So an activity with a
> single five-star review outranks one with fifty reviews averaging 4.8 — and,
> under the decision above, shows "New" while doing it.

It listed three options and assigned the choice to whoever owns `review` in
Phase 12. The Product Owner has since taken the decision earlier: this must not
reach a public page as it stands, and the confidence-weighted mean was the
option named.

Taking it now rather than in Phase 12 is right for the reason the ADRs
themselves were pulled forward — four screens are about to be built against
this ordering, and §16.5 is published in the help centre *"so that providers
understand exactly how placement is earned"*. Changing a published ordering
after providers have read it is a different and worse conversation.

## Decision

**The ranking term is the *displayable* rating.** A subject whose
`rating_count` is below `review.min_display_count` ranks as unrated — the term
evaluates to `NULL`, and `NULLS LAST` puts it behind every subject that has
earned a mean.

Concretely, `domain.ranking.rank_key` now feeds its rating term through
`displayable_rating`, and `selectors._resolve` compiles the same rule to
`CASE WHEN rating_count >= :threshold THEN rating_avg ELSE NULL END`.

**Ranking and display are now the same function.** A subject ranks on exactly
the value it is permitted to show. That is the property that makes this
unviolatable rather than documented: there is no longer a raw mean anywhere for
a future caller to sort by, and `test_ranking_and_display_are_the_same_rule`
fails if the two ever diverge again.

`min_display_count` is a **required keyword** on `rank_key`. A default would let
a caller rank on an ungated mean without noticing, which is precisely the gap
being closed. Making it required meant every existing call site failed to
compile, which is the point — each one had to state its intent.

## Why not the confidence-weighted mean

The weighted mean was the option named, and I am not implementing it. Both
options close the gap; this one closes it without a problem the other one
brings, and that problem is specific rather than a matter of taste.

**A Bayesian shrinkage estimator breaks keyset pagination.** The standard form
is `(v/(v+m))·R + (m/(v+m))·C`, where `C` is the mean across all subjects. `C`
is a *global* aggregate that moves every time any review is published anywhere
in the catalogue. The §9.1 cursor encodes each ordering term's value at the page
boundary, so if `C` shifts between the request for page 1 and the request for
page 2, every row's sort key shifts with it and the keyset predicate silently
skips or repeats rows. That is the exact failure the cursor fingerprint was
built to prevent — a page of ordinary-looking rows with an arbitrary set
missing, which nothing downstream can detect — and a weighted mean would
reintroduce it through the front door.

The threshold gate has no such property. It is computed from values already in
the row, so a subject's rank changes only when that subject's own reviews
change.

Three lesser reasons, in order of weight:

* **It stays inside §3.5.** §16.5 requires *"an explicit, reproducible
  expression — never a learned model"*, and §3.5 makes that a release gate. A
  shrinkage estimator is not machine learning and would not violate it, but it
  introduces a prior mean and a confidence constant that need explaining to a
  provider disputing their placement. "You rank once three people have reviewed
  you" is explicable at a counter; a weighted mean against a drifting global
  prior is not.
* **It makes `NULLS LAST` load-bearing.** ADR 0015 noted that the rating term's
  explicit null handling was vestigial once the column became `NOT NULL DEFAULT
  0.00`. It is now the mechanism, so an accidental `NULLS FIRST` — PostgreSQL's
  default for `DESC`, which is why the flag is carried explicitly — surfaces as
  every unrated subject leading the page rather than as nothing at all.
* **It makes `Activity` consistent with the rest.** `Attraction`,
  `Accommodation` and `Destination` already rank their rating term as a `NULL`
  constant, because none of them has reviews. `Activity` was the only model
  ranking on a real column, and now the difference between them is data rather
  than shape.

**If the weighted mean is still wanted, this does not block it.** It would need
`C` denormalised into a settings row recomputed on a schedule rather than read
live, so that it is stable for the lifetime of a cursor — at which point the
fingerprint must include it, exactly as it now includes the threshold. That is a
real design with a real cost, and it belongs with Phase 12's review work rather
than being assembled here to a deadline.

## Consequences

**§16.5 changes, so the SRS changes.** Amended to v1.4: the fourth ORDER BY
term becomes the gated expression, with BR-127's threshold named. The published
help-centre wording has to say "rating, once a subject has enough published
reviews to have one" rather than "rating", and that is a better sentence to have
to write now than in Phase 12.

**The cursor fingerprint now includes `min_display_count`.** The threshold does
not change *which* terms exist, so the digest would not otherwise have moved —
but it changes what one of them evaluates to, and an administrator lowering it
mid-scroll would move rows across an already-issued page boundary. A cursor
issued under the old threshold is now refused rather than honoured. This is the
same reasoning that put the sort in the fingerprint.

**A test was inverted rather than deleted.**
`test_it_does_not_alter_the_ranking_inputs` pinned the *old* behaviour and was
written precisely so that changing it would be visible. It failed in the commit
that made this change, which is what it was for; it is now
`test_a_thin_five_star_does_not_outrank_an_established_four_eight`, asserting
the opposite. The pair is the record.

**Nothing observable changes in Phase 3.** There are no reviews, so every
subject is below the threshold and every rating term is already `NULL` — the
ordering falls through to price and `id` exactly as before. This is a change
made while it costs nothing, which is the only comfortable time to change a
published ordering.
