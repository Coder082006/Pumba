/**
 * The planner's running total — SRS §24.14, §10.7, §15.3, ADR 0013.
 *
 * This exists because of a real report: *"i planed the day 1 and the day 4 but
 * i see the place of estimated cost is still reading as zero"*. The figure was
 * right. A stay has no price (ADR 0013), an attraction's entry is paid at the
 * gate and excluded from any subtotal (§15.3), and a transfer has no fare until
 * §12.4 — so an itinerary of those three costs exactly 0.00. The screen was
 * reporting a true number in a way nobody could tell from broken pricing, which
 * is the thing these tests pin down.
 */

import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RunningTotal } from '@/components/trip/running-total';
import type { ItineraryItem } from '@/lib/trips';

function item(over: Partial<ItineraryItem> = {}): ItineraryItem {
  return {
    public_id: crypto.randomUUID(),
    day_number: 1,
    sequence_no: 1,
    item_type: 'STAY',
    title: 'Zanzibar Serena Hotel',
    starts_at: '2026-09-05T12:00:00Z',
    ends_at: '2026-09-05T13:00:00Z',
    accommodation: null,
    activity: null,
    attraction: null,
    origin_destination: null,
    target_destination: null,
    distance_m: null,
    travel_seconds: null,
    estimate_quality: null,
    is_approximate: false,
    quantity: 1,
    pax_count: null,
    unit_price: null,
    line_total: null,
    currency: null,
    is_locked: false,
    ...over,
  } as ItineraryItem;
}

function renderTotal(items: ItineraryItem[], amount = '0.00') {
  render(
    <RunningTotal items={items} amount={amount} currency="TZS" summaryHref="/trips/t/summary" />,
  );
}

function renderQuoted(over: { expiresAt?: string | null; items?: ItineraryItem[] } = {}) {
  render(
    <RunningTotal
      items={over.items ?? [item({ item_type: 'ACTIVITY', line_total: '150000.00' })]}
      amount="177000.00"
      currency="TZS"
      summaryHref="/trips/t/summary"
      status="PRICED"
      expiresAt={over.expiresAt === undefined ? in20Minutes() : over.expiresAt}
    />,
  );
}

function in20Minutes(): string {
  return new Date(Date.now() + 20 * 60_000).toISOString();
}

describe('when something in the trip is priced', () => {
  it('shows the figure the server computed', () => {
    renderTotal([item({ item_type: 'ACTIVITY', line_total: '150000.00' })], '150000.00');
    expect(screen.getByText(/Estimated so far/)).toBeDefined();
    expect(screen.getByText(/150,000/)).toBeDefined();
  });

  it('still shows it when the priced line happens to be zero', () => {
    /**
     * A line priced at nothing is a claim the pricing path made — free entry,
     * a waived fee — and it is not the same as a line with no price. `0.00`
     * belongs on the figure side of the branch.
     */
    renderTotal([item({ item_type: 'ACTIVITY', line_total: '0.00' })]);
    expect(screen.getByText(/Estimated so far/)).toBeDefined();
  });
});

describe('when nothing in the trip carries a price', () => {
  const unpriced = [
    item({ item_type: 'STAY' }),
    item({ item_type: 'ATTRACTION', title: 'Darajani Market' }),
    item({ item_type: 'TRANSFER', title: 'Stone Town to Nungwi' }),
  ];

  it('does not show a total of zero', () => {
    /** The defect, stated. A bare "TZS 0.00" reads as broken pricing. */
    renderTotal(unpriced);
    expect(screen.queryByText(/Estimated so far/)).toBeNull();
    expect(screen.queryByText(/0\.00/)).toBeNull();
  });

  it('says the trip is unpriced rather than free', () => {
    renderTotal(unpriced);
    expect(screen.getByText('Nothing priced yet')).toBeDefined();
  });

  it('says who is actually paid, so the zero is not read as everything', () => {
    renderTotal(unpriced);
    expect(screen.getByText(/paid where you go/)).toBeDefined();
  });
});

describe('when the trip is empty', () => {
  it('asks for something to plan instead of explaining pricing', () => {
    renderTotal([]);
    expect(screen.getByText(/Add a stay or something to do/)).toBeDefined();
  });
});

describe('the way onwards', () => {
  it('is offered in every state, because the summary explains the rest', () => {
    renderTotal([]);
    expect(screen.getByRole('link', { name: 'Continue' }).getAttribute('href')).toBe(
      '/trips/t/summary',
    );
  });
});


describe('once the trip is priced', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('stops calling the figure an estimate', () => {
    /**
     * Two different promises. An estimate is arithmetic over a plan; a quote
     * is an offer with seats behind it and a clock on it (§9.4.5), and only
     * one of them expires.
     */
    renderQuoted();
    expect(screen.queryByText(/Estimated so far/)).toBeNull();
  });

  it('counts down to the moment the hold ends', () => {
    renderQuoted();
    expect(screen.getByText(/Seats held for/)).toBeDefined();
    expect(screen.getByText(/^19:5\d$|^20:00$/)).toBeDefined();
  });

  it('ticks rather than showing a fixed instant', () => {
    /**
     * A static "expires at 14:32" is a subtraction the tourist has to do
     * against their own clock, and the point of a twenty-minute hold is that
     * it is short enough to matter.
     */
    renderQuoted();
    const before = screen.getByText(/^\d+:\d\d$/).textContent;
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByText(/^\d+:\d\d$/).textContent).not.toBe(before);
  });

  it('says the price is held, not the seats, when nothing is holdable', () => {
    /**
     * A trip of stays and attractions loses only its price when the clock
     * runs out. Saying "seats held" would make a tourist hurry for nothing.
     */
    renderQuoted({ items: [item({ item_type: 'ATTRACTION', line_total: '0.00' })] });
    expect(screen.getByText(/Price held for/)).toBeDefined();
  });

  it('says so once the hold has lapsed', () => {
    /**
     * §17.5's sweeper runs every sixty seconds, so the browser knows the hold
     * is dead before the server has caught up. Better the true sentence than
     * a countdown to a negative number.
     */
    renderQuoted({ expiresAt: new Date(Date.now() - 1000).toISOString() });
    expect(screen.getByText(/expired/)).toBeDefined();
  });
});

describe('asking for a price', () => {
  it('is offered as a deliberate act', () => {
    /**
     * §9.4.5 takes row locks other tourists queue behind and holds seats for
     * twenty minutes; §9.6 throttles it at twenty an hour per trip. Quoting
     * automatically after every edit would hold inventory for somebody who
     * was still deciding.
     */
    const onQuote = vi.fn();
    render(
      <RunningTotal
        items={[item({ item_type: 'ACTIVITY', line_total: '1.00' })]}
        amount="1.00"
        currency="TZS"
        summaryHref="/trips/t/summary"
        onQuote={onQuote}
      />,
    );
    screen.getByRole('button', { name: 'Get a price' }).click();
    expect(onQuote).toHaveBeenCalled();
  });

  it('offers a refresh once there is already a price', () => {
    render(
      <RunningTotal
        items={[item({ item_type: 'ACTIVITY', line_total: '1.00' })]}
        amount="1.00"
        currency="TZS"
        summaryHref="/trips/t/summary"
        status="PRICED"
        expiresAt={in20Minutes()}
        onQuote={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Refresh the price' })).toBeDefined();
  });

  it('is not offered where the screen cannot do it', () => {
    /** The summary screen renders the same totals and does not quote. */
    renderTotal([item({ item_type: 'ACTIVITY', line_total: '1.00' })], '1.00');
    expect(screen.queryByRole('button', { name: /price/i })).toBeNull();
  });
});
