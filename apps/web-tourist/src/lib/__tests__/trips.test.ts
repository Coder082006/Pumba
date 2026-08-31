/**
 * Trip client helpers — SRS §10.6, §24.14.
 *
 * Three small functions, and each one is a rule the screens depend on rather
 * than a convenience:
 *
 * * `findingsFor` is what puts a warning against the row it is about. §10.6
 *   carries `item_ids` *"so the client can render an inline fix affordance"*,
 *   and the failure mode is silent — a wrong filter renders an empty list, not
 *   an error, and the finding simply never appears.
 * * `tripLevelFindings` is the other half. VR-16 is about nights that have *no*
 *   stay, so it names no item; if it were dropped rather than shown in the
 *   banner it would vanish entirely.
 * * `byDay` decides what order a day's items appear in. §10.4 assigns
 *   `sequence_no` deliberately, and a client that sorted by anything else
 *   would quietly contradict the plan it was given.
 */

import { describe, expect, it } from 'vitest';

import { byDay, findingsFor, tripLevelFindings } from '../trips';
import type { Finding, ItineraryItem } from '../trips';

function item(overrides: Partial<ItineraryItem> = {}): ItineraryItem {
  return {
    public_id: 'a',
    day_number: 1,
    sequence_no: 1,
    item_type: 'ACTIVITY',
    title: 'Kayak',
    starts_at: '2027-06-01T09:00:00Z',
    ends_at: '2027-06-01T10:00:00Z',
    quantity: 1,
    is_locked: false,
    is_approximate: false,
    ...overrides,
  } as ItineraryItem;
}

function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    code: 'VR-03',
    severity: 'ERROR',
    message: 'Not enough time.',
    item_ids: [],
    suggested_action: 'NONE',
    details: {},
    ...overrides,
  } as Finding;
}

describe('findingsFor', () => {
  it('returns the findings that name the item', () => {
    const target = item({ public_id: 'x' });
    const mine = finding({ item_ids: ['x'] });
    const theirs = finding({ item_ids: ['y'] });

    expect(findingsFor(target, [mine, theirs])).toEqual([mine]);
  });

  it('returns nothing for an item nobody named', () => {
    expect(findingsFor(item({ public_id: 'x' }), [finding({ item_ids: ['y'] })])).toEqual([]);
  });

  it('matches an item named alongside others', () => {
    // VR-02 names both halves of an overlap, VR-10 names every mispriced item.
    // Filtering on the first id only would drop the finding from every row but
    // one, which reads as the problem having been half-fixed.
    const overlap = finding({ code: 'VR-02', item_ids: ['a', 'b'] });
    expect(findingsFor(item({ public_id: 'b' }), [overlap])).toEqual([overlap]);
  });
});

describe('tripLevelFindings', () => {
  it('keeps the ones that name no item', () => {
    const banner = finding({ code: 'VR-16', item_ids: [] });
    const inline = finding({ item_ids: ['a'] });

    expect(tripLevelFindings([banner, inline])).toEqual([banner]);
  });

  it('is the complement of findingsFor, so nothing is shown twice or lost', () => {
    /**
     * The property that matters more than either function alone.
     *
     * Every finding has to appear exactly once: inline if it names an item,
     * in the banner if it does not. An overlap would double-report a problem
     * and a gap would hide one, and both look plausible on screen.
     */
    const rows = [item({ public_id: 'a' }), item({ public_id: 'b' })];
    const findings = [
      finding({ code: 'VR-03', item_ids: ['a'] }),
      finding({ code: 'VR-02', item_ids: ['a', 'b'] }),
      finding({ code: 'VR-16', item_ids: [] }),
    ];

    const shown = new Set<string>();
    for (const row of rows) {
      for (const f of findingsFor(row, findings)) shown.add(f.code);
    }
    for (const f of tripLevelFindings(findings)) shown.add(f.code);

    expect(shown).toEqual(new Set(['VR-03', 'VR-02', 'VR-16']));
  });
});

describe('byDay', () => {
  it('groups items and orders each day by sequence_no', () => {
    const grouped = byDay([
      item({ public_id: 'c', day_number: 2, sequence_no: 1 }),
      item({ public_id: 'b', day_number: 1, sequence_no: 2 }),
      item({ public_id: 'a', day_number: 1, sequence_no: 1 }),
    ]);

    expect([...grouped.keys()]).toEqual([1, 2]);
    expect(grouped.get(1)?.map((i) => i.public_id)).toEqual(['a', 'b']);
  });

  it('orders the days themselves', () => {
    // Day 10 after day 9, not before it — the failure a string sort produces
    // and which only appears on a trip longer than nine days.
    const grouped = byDay([
      item({ day_number: 10 }),
      item({ day_number: 9 }),
      item({ day_number: 2 }),
    ]);
    expect([...grouped.keys()]).toEqual([2, 9, 10]);
  });

  it('is empty for an itinerary with no items', () => {
    expect(byDay([]).size).toBe(0);
  });
});
