/**
 * The two pieces of arithmetic behind "Add to trip" — SRS §13.1, §10.4.
 *
 * Neither is visible on screen and both are silently wrong in the same way if
 * they are wrong at all: an item lands on the day next to the one the tourist
 * chose. That is precisely the defect that appeared server-side earlier in this
 * phase, where `timezone.localtime()` used the server's zone and filed every
 * evening item under the previous day for every non-UTC destination.
 */

import { describe, expect, it } from 'vitest';

import { daysBetween, instantAt } from '@/components/trip/add-to-trip';

describe('instantAt', () => {
  it('reads the time as the destination’s, not the browser’s', () => {
    // Zanzibar is UTC+3 all year. 18:00 there is 15:00 UTC — and a client that
    // took the wall time for UTC would store 18:00Z, which is 21:00 local.
    expect(instantAt('2027-08-12', '18:00', 'Africa/Dar_es_Salaam')).toBe(
      '2027-08-12T15:00:00.000Z',
    );
  });

  it('keeps a late evening on its own local day', () => {
    // The failure this exists to prevent: 23:30 local is 20:30Z the same day,
    // but 23:30 read as UTC would be 02:30 local *the next day*, and §10.4
    // would sequence it into a day the tourist never picked.
    expect(instantAt('2027-08-12', '23:30', 'Africa/Dar_es_Salaam')).toBe(
      '2027-08-12T20:30:00.000Z',
    );
  });

  it('uses the offset in force on that date, not today’s', () => {
    // A zone that observes DST. 12:00 in London is 11:00Z in January and
    // 12:00Z is wrong by an hour in July — the reason the offset is measured
    // at the instant rather than taken once.
    expect(instantAt('2027-01-15', '12:00', 'Europe/London')).toBe('2027-01-15T12:00:00.000Z');
    expect(instantAt('2027-07-15', '12:00', 'Europe/London')).toBe('2027-07-15T11:00:00.000Z');
  });

  it('handles a zone behind UTC', () => {
    expect(instantAt('2027-08-12', '09:00', 'America/New_York')).toBe('2027-08-12T13:00:00.000Z');
  });
});

describe('daysBetween', () => {
  it('counts whole days, so day one is the trip’s start date', () => {
    expect(daysBetween('2027-08-12', '2027-08-12')).toBe(0);
    expect(daysBetween('2027-08-12', '2027-08-16')).toBe(4);
  });

  it('counts across a month boundary', () => {
    expect(daysBetween('2027-08-30', '2027-09-02')).toBe(3);
  });

  it('is negative for a date before the trip', () => {
    // What makes "those dates are outside this trip" reachable rather than a
    // day number of zero quietly reaching the API.
    expect(daysBetween('2027-08-12', '2027-08-10')).toBe(-2);
  });
});
