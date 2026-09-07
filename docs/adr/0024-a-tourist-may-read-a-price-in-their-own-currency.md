# ADR 0024 — A tourist may read a price in their own currency; they are still charged in the destination's

**Status:** Accepted · **Date:** 2026-09-07 · **Phase:** 6

## Context

Every price this platform shows is in Tanzanian shillings. A planned trip reads
`TZS 94,500`. The tourists it is built for arrive from Germany, the United
Kingdom and the United States, and none of them can tell whether that is
cheap without leaving the page.

The mechanism to fix it is already built and connected to nothing:

- `common/middleware.py` parses `X-Currency` onto `request.presentment_currency`
  and patches `Vary`, so a CDN cannot serve one tourist's currency to another.
  **No view reads that attribute.**
- `ports/exchange_rate.py` declares `ExchangeRatePort`; `ports/fakes.py`
  implements it with a deterministic cross-rate table that already carries USD,
  EUR, GBP, TZS, KES, ZAR, NZD and CLP; `ports_registry.get_exchange_rate_port()`
  resolves it.
- `common/display_money.py` has `IndicativeAmount` and `convert_for_display()`.
  **`convert_for_display` appears only in docstrings.** Nothing calls it.
- `currency.enabled` is already `["USD", "EUR", "GBP", "TZS"]` and already
  served publicly as `enabled_currencies` by `GET /config`.
- `tourist_profile.preferred_currency` exists, defaults to `USD`, and is
  accepted at registration.

So the question is not whether to build a currency subsystem. It is where to
draw the line between *reading* a price in euros and *paying* in euros, which
the specification treats as two entirely different mechanisms and which a
careless implementation would merge.

**§18.4** gives the paying half its own machinery: `fx_rate` rows refreshed
hourly with `fx.markup_percent`, "frozen at `priced_at` and stored on the trip
and on every derived record", so "a trip's total can therefore never change
because of a rate move between quoting and payment". §20.6: "every conversion
in the system stores its rate, source and timestamp — there are no untraceable
conversions." §21.7 makes the presentment currency the one the PSP charges in,
"constrained to the set the PSP supports for their country".

`fx_rate` belongs to `finance`, which is Phase 8, and the PSP is Appendix D-1,
undecided.

**§38 lists "multi-currency presentment beyond USD and TZS" as out of v1
scope.**

## Decision

**A tourist chooses a display currency and every price they see is rendered in
it. The trip is priced and charged in `destination.default_currency`, exactly
as it is today.**

### 1. The display figure refuses to be money, and stays that way

`convert_for_display()` returns an `IndicativeAmount` whose `__add__`,
`__sub__`, `__mul__` and the rest raise `IndicativeAmountError` by
construction. That is not an inconvenience to work around; it is the decision,
expressed in the type. A converted figure cannot be summed into a subtotal by
accident, because it will not add.

Totals continue to be computed in the listing currency with `Money`, and the
converted figure is derived from the final total rather than from its parts.
Converting each line and summing would produce a number that disagrees with
the total by a few cents and would be defended forever as a rounding quirk.

### 2. Every money field gains a sibling, and the real price never leaves

The wire shape is `{"amount", "currency", "display": {...}}`. The `display`
object carries the converted amount, its currency, the rate, the rate's
`as_of` and its `source`. The charged figure is always present and always
first.

`display` is **absent** — not null, not zero — when no conversion applies or no
rate is available. `ExchangeRatePort.indicative_rate()` returns `None` rather
than raising for exactly this: the page shows the listing's own currency and
says nothing about rates. No stale rate, no hard-coded one, no guess. The same
discipline ADR 0019 applied to the distance chip: a figure a tourist could
mistake for fact is never fabricated.

### 3. The screen says which one is real

Beside every converted figure: the charged amount, the word "approx.", and the
date of the rate. A tourist who sees `EUR 86.94` and pays `TZS 94,500` has not
been surprised, because the page told them which number the card would be
charged in.

### 4. The enabled set is data, and is finally enforced

Four currencies: USD, EUR, GBP, TZS — the value `currency.enabled` already
holds. Adding CHF later is an administrator changing a `system_setting` row,
with no migration and no deployment.

§9.4.1 requires `preferred_currency` to be "in the enabled set" and
`validate_preferred_currency` does not check it. That is fixed here: a
preference for a currency the platform cannot show is rejected at the boundary
rather than silently producing pages with no `display` object on them.

### 5. This exceeds §38, deliberately, and only on the display side

§38's exclusion is about presentment in §21.7's sense — the currency the charge
is made in, which the PSP has to support and settle. Nothing here changes what
is charged, what is stored, or what any ledger row will say. `trip.currency`
remains `destination.default_currency`; BR-016's lock at first pricing is
untouched; VR-10's uniformity rule is untouched.

What does exceed §38 is showing GBP and EUR at all. §9.1's `X-Currency` and
§24.1's chooser are not limited to two currencies, and §24.11 explicitly wants
a tourist to "compare against prices at home" — but the exclusion list says
what it says, and the honest thing is to record the deviation as an amendment
rather than let it drift in behind a feature.

**Recorded as an SRS amendment: v1.6 — display currency is any currency in
`currency.enabled`; presentment and settlement currency remain out of v1 scope
beyond USD and TZS, and arrive with Phase 8 and Appendix D-1.**

## Consequences

**A tourist can finally judge the price.** That is the whole product reason,
and it costs one serializer mixin and a header control, because the hard parts
were built in Phase 1 and left unwired.

**Phase 8 inherits a clean seam rather than a mess to unpick.** When
`finance.fx_rate` lands, the paying path is new code beside this one, not a
promotion of it — because the display type cannot be promoted. Somebody who
tries will get an `IndicativeAmountError` naming §18.4, which is the most
useful error this codebase could give them.

**The failure mode to watch is a converted figure escaping into a total.** It
is the same shape as ADR 0019's quality laundering: a number that was labelled
approximate at one layer and is arithmetic by the next. The guard is the type,
and the test is that summing a list of `IndicativeAmount` raises — asserted
directly, so that anyone who "fixes" it by adding `__add__` fails the suite
rather than shipping.

**Rates come from a fake, and every figure carries its source.** No provider is
selected. The `source` field on the wire says `"fake"` in development, which is
ugly and correct: a tourist-facing figure whose provenance is a stub should
look like one to anybody inspecting the response, and the field is what makes
choosing a real feed a configuration change rather than a search through
templates.
