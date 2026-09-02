'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
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
 *
 * **Phase 5 gave the footer an action.** Until the quote existed the total was
 * provisional and there was nothing to do about it; now `POST /trips/{id}/quote`
 * holds the seats behind it for twenty minutes, and §24.20 counts down. A
 * priced trip therefore shows the offer and its clock rather than an estimate,
 * because those are different promises and only one of them expires.
 */
export function RunningTotal({
  items,
  amount,
  currency,
  summaryHref,
  status,
  expiresAt,
  busy = false,
  onQuote,
}: {
  items: readonly ItineraryItem[];
  amount: string;
  currency: string;
  summaryHref: string;
  /** §20.5's state. `PRICED` means the seats are held and the clock is running. */
  status?: string;
  /** `trip.quote_expires_at`, when there is a live offer. */
  expiresAt?: string | null;
  busy?: boolean;
  onQuote?: () => void;
}) {
  // `line_total` is null for everything the platform does not charge for, which
  // is the same test §24.21's breakdown groups by. A present "0.00" is a priced
  // line that costs nothing and stays on the figure side of this branch.
  const priced = items.some((item) => item.line_total);
  const holdable = items.some((item) => item.item_type === 'ACTIVITY');
  const quoted = status === 'PRICED';

  return (
    <footer className="fixed inset-x-0 bottom-0 border-t border-border bg-background/95 backdrop-blur">
      <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="text-sm text-muted-foreground">
          {quoted ? (
            <p>
              <span className="font-medium text-foreground">
                <Money value={{ amount, currency }} />
              </span>
              <span className="block text-xs">
                <Countdown until={expiresAt ?? null} holding={holdable} />
              </span>
            </p>
          ) : priced ? (
            <p>
              Estimated so far ·{' '}
              <span className="font-medium text-foreground">
                <Money value={{ amount, currency }} />
              </span>
              <span className="block text-xs">
                Nothing is held until you ask for a price.
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

        <div className="flex items-center gap-2">
          {onQuote ? (
            <button
              type="button"
              disabled={busy}
              onClick={onQuote}
              className="rounded-md border border-border px-5 py-2 text-sm font-semibold transition-colors duration-fast ease-out hover:bg-muted disabled:opacity-60"
            >
              {busy ? 'Pricing…' : quoted ? 'Refresh the price' : 'Get a price'}
            </button>
          ) : null}
          <Link
            href={summaryHref}
            className="rounded-md bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground"
          >
            Continue
          </Link>
        </div>
      </div>
    </footer>
  );
}

/**
 * How long this offer stands — §24.20, §17.2.
 *
 * **It ticks.** A static "expires at 14:32" is a number somebody has to
 * subtract from their own clock, and the whole point of a twenty-minute hold is
 * that it is short enough to matter. It re-renders once a second, which is
 * cheap for one element and is the only place in the app that does.
 *
 * **It says what expires.** A trip whose activities are held loses seats when
 * this runs out; a trip of stays and attractions loses only the price. The
 * sentence differs because the consequence does, and a tourist who thinks
 * seats are at stake when they are not will hurry for no reason.
 */
function Countdown({ until, holding }: { until: string | null; holding: boolean }) {
  const [left, setLeft] = useState(() => remaining(until));

  useEffect(() => {
    setLeft(remaining(until));
    if (until === null) return;
    const timer = setInterval(() => setLeft(remaining(until)), 1000);
    return () => clearInterval(timer);
  }, [until]);

  if (until === null) return <>Priced. Nothing is held.</>;
  if (left <= 0) {
    // The sweeper runs every sixty seconds (§17.5), so there is a window where
    // the browser knows the hold is dead and the server has not caught up. Say
    // the true thing rather than a countdown to a negative number.
    return <>This price has expired — ask again to hold the seats.</>;
  }
  return (
    <>
      {holding ? 'Seats held for ' : 'Price held for '}
      <span className="font-medium tabular-nums text-foreground">{clock(left)}</span>
    </>
  );
}

function remaining(until: string | null): number {
  if (until === null) return 0;
  return Math.max(0, Date.parse(until) - Date.now());
}

function clock(ms: number): string {
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}
