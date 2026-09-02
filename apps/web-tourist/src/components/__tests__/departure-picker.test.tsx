/**
 * The departure picker — SRS §24.10, §16.2, §17.1.
 *
 * Four properties, and none of them is "it renders a list".
 *
 * **It must not promise a seat.** §17.1 I3 draws the line between a search
 * figure and an authoritative one, and this component sits on the wrong side of
 * it deliberately. The copy has to say so, because a tourist who reads "4 left"
 * as a reservation and finds the boat full has been misled by a number that was
 * true when it was rendered.
 *
 * **It must not hide a date.** A sold-out or cancelled departure is shown and
 * labelled. Omitting it reads as a bug to somebody who was looking at that date
 * a minute ago, and a tourist deciding between two weeks needs to see which one
 * is full.
 *
 * **It must group in the destination's zone.** An 08:30 Zanzibar departure is
 * 05:30 in London. Grouping on the viewer's clock files it under the previous
 * day, and the tourist books the wrong date from a calendar that looks right.
 *
 * **The reasons are different sentences.** "Sold out" says try another date;
 * "too late" says any date but this one; "too many people" says no date will
 * work. Collapsing them into "unavailable" makes a tourist try three more.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DeparturePicker } from '@/components/catalogue/departure-picker';
import type * as Inventory from '@/lib/inventory';
import type { Departure } from '@/lib/inventory';

const listDepartures = vi.fn();
// `importActual` rather than a bare object: `byLocalDate`, `scarcity` and
// `unbookableLabel` are the module's own and are what the component renders
// with. Stubbing them too would leave the test asserting against copies.
vi.mock('@/lib/inventory', async () => {
  const actual = await vi.importActual<typeof Inventory>('@/lib/inventory');
  return { ...actual, listDepartures: (...args: unknown[]) => listDepartures(...args) };
});

/** Zanzibar is UTC+3 and never observes DST. 05:30Z is 08:30 there. */
const ZONE = 'Africa/Dar_es_Salaam';

function departure(over: Partial<Departure> = {}): Departure {
  return {
    public_id: crypto.randomUUID(),
    departs_at: '2027-08-12T05:30:00Z',
    status: 'OPEN',
    remaining: 8,
    basis: 'INDICATIVE',
    price_override: null,
    unbookable: null,
    is_bookable: true,
    ...over,
  } as Departure;
}

function show(departures: Departure[], onChange = vi.fn(), value = '') {
  listDepartures.mockResolvedValue(departures);
  render(
    <DeparturePicker reference="mnemba" timeZone={ZONE} value={value} onChange={onChange} />,
  );
  return onChange;
}

beforeEach(() => {
  listDepartures.mockReset();
});

describe('when there are departures', () => {
  it('offers each one as a choice', async () => {
    show([departure()]);
    expect(await screen.findByRole('button', { name: /08:30/ })).toBeDefined();
  });

  it('shows the time at the destination, not the viewer', async () => {
    /** 05:30Z is 08:30 in Zanzibar. A viewer's-clock render would say 05:30
     * to somebody in London and send them to the jetty three hours early. */
    show([departure({ departs_at: '2027-08-12T05:30:00Z' })]);
    expect(await screen.findByText('08:30')).toBeDefined();
  });

  it('reports the choice as the departure instant', async () => {
    /** Not an id. The instant is what the item stores, and
     * UNIQUE(activity_id, departs_at) turns it back into a departure at quote
     * time — so no client ever handles an internal identifier (ADR 0022). */
    const onChange = show([departure({ departs_at: '2027-08-12T05:30:00Z' })]);
    (await screen.findByRole('button', { name: /08:30/ })).click();
    expect(onChange).toHaveBeenCalledWith('2027-08-12T05:30:00Z');
  });

  it('marks the chosen one for a screen reader', async () => {
    show([departure({ departs_at: '2027-08-12T05:30:00Z' })], vi.fn(), '2027-08-12T05:30:00Z');
    const button = await screen.findByRole('button', { name: /08:30/ });
    expect(button.getAttribute('aria-pressed')).toBe('true');
  });

  it('never says the seats are reserved', async () => {
    show([departure()]);
    await screen.findByRole('button', { name: /08:30/ });
    expect(screen.getByText(/indicative/i)).toBeDefined();
    expect(screen.getByText(/held for you when you ask for a price/i)).toBeDefined();
  });
});

describe('a departure this party cannot take', () => {
  it('is shown rather than hidden', async () => {
    show([departure({ unbookable: 'SOLD_OUT', is_bookable: false, remaining: 0 })]);
    expect(await screen.findByRole('button', { name: /08:30/ })).toBeDefined();
  });

  it('cannot be chosen', async () => {
    const onChange = show([
      departure({ unbookable: 'SOLD_OUT', is_bookable: false, remaining: 0 }),
    ]);
    const button = await screen.findByRole('button', { name: /08:30/ });
    expect(button.hasAttribute('disabled')).toBe(true);
    button.click();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('says sold out when it is full', async () => {
    show([departure({ unbookable: 'SOLD_OUT', is_bookable: false, remaining: 0 })]);
    expect(await screen.findByText('Sold out')).toBeDefined();
  });

  it('says something different when it is too late', async () => {
    /** A different action: sold out wants another date, this wants any date
     * at all. One word for both would send a tourist round the calendar. */
    show([departure({ unbookable: 'PAST_CUTOFF', is_bookable: false })]);
    expect(await screen.findByText('Too late to book')).toBeDefined();
  });

  it('says something different again when the party is too large', async () => {
    /** The one a calendar must not let somebody discover four dates later:
     * no date will work. */
    show([departure({ unbookable: 'PARTY_TOO_LARGE', is_bookable: false })]);
    expect(await screen.findByText('Too many people')).toBeDefined();
  });

  it('labels a cancelled departure as cancelled rather than as full', async () => {
    show([departure({ status: 'CANCELLED', unbookable: 'CANCELLED', is_bookable: false })]);
    expect(await screen.findByText('Cancelled')).toBeDefined();
  });
});

describe('scarcity', () => {
  it('is coarse when a few are left', async () => {
    show([departure({ remaining: 2 })]);
    expect(await screen.findByText('Only 2 left')).toBeDefined();
  });

  it('says nothing precise when there is plenty', async () => {
    show([departure({ remaining: 9 })]);
    expect(await screen.findByText('Seats available')).toBeDefined();
  });
});

describe('when the operator has published nothing', () => {
  it('says so instead of showing an empty calendar', async () => {
    show([]);
    expect(await screen.findByText(/No departures are published/)).toBeDefined();
  });
});

describe('when the request fails', () => {
  it('does not claim the activity has no dates', async () => {
    /** A failed load and a genuinely empty calendar are different facts, and
     * the second is a commercial claim about a provider. */
    listDepartures.mockRejectedValue(new Error('offline'));
    render(
      <DeparturePicker reference="mnemba" timeZone={ZONE} value="" onChange={vi.fn()} />,
    );
    expect(await screen.findByText(/could not be loaded/)).toBeDefined();
    expect(screen.queryByText(/No departures are published/)).toBeNull();
  });
});

describe('the party size', () => {
  it('is passed to the server so the list is advice', async () => {
    listDepartures.mockResolvedValue([departure()]);
    render(
      <DeparturePicker
        reference="mnemba"
        timeZone={ZONE}
        pax={4}
        value=""
        onChange={vi.fn()}
      />,
    );
    await waitFor(() => expect(listDepartures).toHaveBeenCalledWith('mnemba', { pax: 4 }));
  });
});
