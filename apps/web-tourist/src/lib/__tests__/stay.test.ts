/**
 * BR-101, client side.
 *
 * The cases mirror `apps/api/apps/catalogue/tests/test_domain_pricing.py`
 * deliberately: this module is a second implementation of one rule, and the
 * only thing making that acceptable is that both are pinned to the same
 * examples and bounded by the same `stay.max_nights`.
 *
 * The case that matters most is the one crossing a DST transition. Naive
 * date arithmetic that parses `YYYY-MM-DD` in local time returns 23 or 25
 * hours for one of those days, which floors to the wrong night count — and
 * only for travellers in zones that observe DST, which is nobody testing this
 * from Zanzibar.
 */

import { describe, expect, it } from 'vitest';

import { checkStay, describeNights } from '@/lib/stay';

const LIMITS = { maxNights: 30 };

describe('checkStay', () => {
  it('counts the nights between two dates', () => {
    const result = checkStay('2027-08-12', '2027-08-16', LIMITS);
    expect(result).toEqual({ ok: true, nights: 4 });
  });

  it('accepts a single night', () => {
    expect(checkStay('2027-08-12', '2027-08-13', LIMITS)).toEqual({ ok: true, nights: 1 });
  });

  it('refuses check-out before check-in', () => {
    const result = checkStay('2027-08-16', '2027-08-12', LIMITS);
    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toBe('NOT_AFTER');
  });

  it('refuses equal dates, which are a zero-night stay', () => {
    // BR-101 says *strictly* after. A day trip is a real thing somebody will
    // try to record here, and it is not a stay.
    const result = checkStay('2027-08-12', '2027-08-12', LIMITS);
    expect(result.ok === false && result.reason).toBe('NOT_AFTER');
  });

  it('accepts exactly the maximum', () => {
    const result = checkStay('2027-08-01', '2027-08-31', LIMITS);
    expect(result).toEqual({ ok: true, nights: 30 });
  });

  it('refuses one night beyond it, and says what the bound is', () => {
    const result = checkStay('2027-08-01', '2027-09-01', LIMITS);
    expect(result.ok === false && result.reason).toBe('TOO_LONG');
    expect(result.ok === false && result.message).toContain('30');
    expect(result.ok === false && result.message).toContain('31');
  });

  it('takes the bound from its argument rather than from a constant', () => {
    // The whole reason `maxNights` is a required keyword: an administrator
    // changing `stay.max_nights` must change this form's behaviour without a
    // front-end release.
    expect(checkStay('2027-08-01', '2027-08-10', { maxNights: 7 }).ok).toBe(false);
    expect(checkStay('2027-08-01', '2027-08-10', { maxNights: 14 }).ok).toBe(true);
  });

  it('counts a stay spanning a DST transition correctly', () => {
    // 26 October 2025 is when Europe/London leaves BST. Parsed in local time
    // that Sunday is 25 hours long, so a naive difference gives 3.04 nights
    // for what is plainly 3 — and the bug is invisible to anyone testing from
    // a zone without DST, which includes the destination itself.
    expect(checkStay('2025-10-25', '2025-10-28', LIMITS)).toEqual({ ok: true, nights: 3 });
    // The spring transition, the other direction: a 23-hour day.
    expect(checkStay('2025-03-29', '2025-04-01', LIMITS)).toEqual({ ok: true, nights: 3 });
  });

  it('counts across a month and a year boundary', () => {
    expect(checkStay('2027-12-30', '2028-01-02', LIMITS)).toEqual({ ok: true, nights: 3 });
  });

  it('treats an empty field as incomplete rather than invalid', () => {
    // A form asks this on every keystroke; "you have not finished typing" must
    // not render as an error the moment the first date is picked.
    expect(checkStay('', '2027-08-16', LIMITS).ok === false).toBe(true);
    expect(checkStay('', '2027-08-16', LIMITS)).toMatchObject({ reason: 'INCOMPLETE' });
    expect(checkStay('2027-08-12', '', LIMITS)).toMatchObject({ reason: 'INCOMPLETE' });
  });

  it('refuses a month that does not exist', () => {
    expect(checkStay('2027-13-01', '2027-13-05', LIMITS)).toMatchObject({ reason: 'MALFORMED' });
  });

  it('rolls an over-long day forward, as the platform does', () => {
    // Recorded rather than defended against. `Date.parse` turns 30 February
    // into 2 March, so this reports 3 nights from 2 March, not an error.
    //
    // Left alone on purpose: `<input type="date">` cannot emit this, so
    // reaching it means a hand-crafted value, and the server — which is the
    // authority on BR-101 — will reject or normalise it on submit. Adding a
    // calendar validator here to catch a case the widget cannot produce would
    // be a third implementation of a rule that already has two.
    expect(checkStay('2027-02-30', '2027-03-05', LIMITS)).toEqual({ ok: true, nights: 3 });
  });

  it('refuses text that is not a date at all', () => {
    expect(checkStay('next tuesday', '2027-08-16', LIMITS)).toMatchObject({ reason: 'MALFORMED' });
  });
});

describe('describeNights', () => {
  it('does not say "1 nights"', () => {
    expect(describeNights(1)).toBe('1 night');
    expect(describeNights(4)).toBe('4 nights');
  });
});
