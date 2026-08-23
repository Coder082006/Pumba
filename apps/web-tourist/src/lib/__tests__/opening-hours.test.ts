/**
 * §15.2 requires opening hours be evaluated in the *destination's* timezone.
 *
 * The bug these tests exist for is the invisible one: "Today" resolved against
 * the viewer's clock marks the wrong row for anyone outside the destination's
 * zone, and the page looks entirely normal while doing it. During
 * Zanzibar-only development, on a server sharing that zone, it never shows up.
 *
 * So every case here uses an instant where the two zones disagree about what
 * day it is — which is the only kind of case that can fail.
 */

import { describe, expect, it } from 'vitest';

import { dateIn, weekTable, weekdayIn } from '@/lib/opening-hours';

const ZANZIBAR = 'Africa/Dar_es_Salaam'; // UTC+3, no DST
const LONDON = 'Europe/London';

// 22:30 UTC on Tuesday 12 August 2025 — already Wednesday in Zanzibar.
const LATE_TUESDAY_UTC = new Date('2025-08-12T22:30:00Z');

const HOURS = {
  mon: [],
  tue: [['09:00', '18:00']],
  wed: [['09:00', '18:00']],
  thu: [['09:00', '18:00']],
  fri: [
    ['09:00', '12:00'],
    ['14:00', '18:00'],
  ],
  sat: [['09:00', '18:00']],
  sun: [],
  exceptions: [{ date: '2025-08-13', closed: true, reason: 'Public holiday' }],
};

describe('the day is resolved in the destination zone', () => {
  it('is already Wednesday in Zanzibar while it is still Tuesday in London', () => {
    expect(weekdayIn(ZANZIBAR, LATE_TUESDAY_UTC)).toBe('wed');
    expect(weekdayIn(LONDON, LATE_TUESDAY_UTC)).toBe('tue');
  });

  it('resolves the calendar date the same way', () => {
    expect(dateIn(ZANZIBAR, LATE_TUESDAY_UTC)).toBe('2025-08-13');
    expect(dateIn(LONDON, LATE_TUESDAY_UTC)).toBe('2025-08-12');
  });

  it('marks today against the destination, not the viewer', () => {
    const rows = weekTable(HOURS, { timeZone: ZANZIBAR, now: LATE_TUESDAY_UTC });
    expect(rows?.[0]?.key).toBe('wed');
    expect(rows?.filter((r) => r.isToday).map((r) => r.key)).toEqual(['wed']);
  });
});

describe('the table', () => {
  it('starts at today and wraps', () => {
    const rows = weekTable(HOURS, { timeZone: ZANZIBAR, now: LATE_TUESDAY_UTC });
    expect(rows?.map((r) => r.key)).toEqual(['wed', 'thu', 'fri', 'sat', 'sun', 'mon', 'tue']);
  });

  it('carries every range of a split day', () => {
    const rows = weekTable(HOURS, { timeZone: ZANZIBAR, now: LATE_TUESDAY_UTC });
    const friday = rows?.find((r) => r.key === 'fri');
    expect(friday?.ranges).toEqual([
      ['09:00', '12:00'],
      ['14:00', '18:00'],
    ]);
  });

  it('renders a closed day as no ranges rather than as missing', () => {
    const rows = weekTable(HOURS, { timeZone: ZANZIBAR, now: LATE_TUESDAY_UTC });
    expect(rows?.find((r) => r.key === 'sun')?.ranges).toEqual([]);
  });

  it('lets an exception outrank the weekly pattern, with its reason', () => {
    // The exception is dated 2025-08-13, which is *today* in Zanzibar and
    // tomorrow in London — so this also fails if the date is resolved wrong.
    const rows = weekTable(HOURS, { timeZone: ZANZIBAR, now: LATE_TUESDAY_UTC });
    const today = rows?.find((r) => r.isToday);
    expect(today?.ranges).toEqual([]);
    expect(today?.exceptionReason).toBe('Public holiday');
  });

  it('does not apply that exception when the viewer zone is used instead', () => {
    // Guards the guard: if this produced the same answer as the test above,
    // neither would be proving the zone is honoured.
    const rows = weekTable(HOURS, { timeZone: LONDON, now: LATE_TUESDAY_UTC });
    const today = rows?.find((r) => r.isToday);
    expect(today?.key).toBe('tue');
    expect(today?.exceptionReason).toBeUndefined();
  });

  it('returns null when an attraction publishes no hours', () => {
    expect(weekTable(null, { timeZone: ZANZIBAR })).toBeNull();
  });

  it('survives a malformed blob rather than throwing on a public page', () => {
    const rows = weekTable({ mon: 'nonsense', tue: [['09:00', '18:00']] }, { timeZone: ZANZIBAR });
    expect(rows?.find((r) => r.key === 'mon')?.ranges).toEqual([]);
  });
});
