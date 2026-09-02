'use client';

import Link from 'next/link';
import { Money } from '@pumba/ui';

import type { ItineraryItem } from '@/lib/trips';

/**
 * §24.14's running total footer — and the three different things a zero means.
 *
 * The figure is the server's. §10.7 computes it with `Decimal` and applies
 * ROUND_HALF_UP once per line and once per aggregate, and §7.5.10 has a
 * database CHECK that the total equals its parts; a client that summed the
 * lines itself would be a second implementation of the pricing path, in
 * floating point, disagreeing in the last cent.
 *
 * **What this component adds is the sentence next to the figure.** In v1 most
 * of a real itinerary is unpriced *by design* and not by omission:
 *
 *   - a STAY carries no price at all — ADR 0013 makes accommodation a location
 *     reference, and `price_item` raises rather than returning zero, precisely
 *     because "free" and "not priced" are different claims;
 *   - an ATTRACTION's entry is paid at the gate and §15.3 excludes it from any
 *     subtotal;
 *   - a TRANSFER has no fare until §12.4's tariff, which is the transport
 *     module;
 *
 * which leaves ACTIVITY as the only kind that moves the number. So a tourist
 * who plans two days of stays and attractions has a correct, complete,
 * well-formed itinerary whose total is 0.00 — and a footer reading
 * *"Estimated so far · TZS 0.00"* tells them that in a way indistinguishable
 * from broken pricing. Reporting a true figure that reads as a false one is
 * still a defect; it just is not an arithmetic one.
 *
 * The rule is therefore: **show the figure only when something priceable is in
 * the trip**, and otherwise say what is actually true. That is the same choice
 * §24.21's breakdown already makes on the summary screen — "Nothing priced
 * yet" rather than a row of zeroes — and the two screens disagreeing about the
 * same trip would be worse than either wording alone.
 */
export function RunningTotal({
  items,
  amount,
  currency,
  summaryHref,
}: {
  items: readonly ItineraryItem[];
  amount: string;
  currency: string;
  summaryHref: string;
}) {
  // `line_total` is null for everything the platform does not charge for, which
  // is the same test §24.21's breakdown groups by. A present "0.00" is a priced
  // line that costs nothing and stays on the figure side of this branch.
  const priced = items.some((item) => item.line_total);

  return (
    <footer className="fixed inset-x-0 bottom-0 border-t border-border bg-background/95 backdrop-blur">
      <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="text-sm text-muted-foreground">
          {priced ? (
            <p>
              Estimated so far ·{' '}
              <span className="font-medium text-foreground">
                <Money value={{ amount, currency }} />
              </span>
            </p>
          ) : (
            <p>
              <span className="font-medium text-foreground">Nothing priced yet</span>
              <span className="block text-xs">
                {items.length === 0
                  ? 'Add a stay or something to do, then plan the days.'
                  : 'Stays and attraction entry are paid where you go — activities are the part we cost.'}
              </span>
            </p>
          )}
        </div>
        <Link
          href={summaryHref}
          className="rounded-md bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground"
        >
          Continue
        </Link>
      </div>
    </footer>
  );
}
