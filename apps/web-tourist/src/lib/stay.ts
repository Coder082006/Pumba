/**
 * BR-101 on the client — SRS §24.11 validation.
 *
 * The server's `catalogue.domain.pricing.stay_nights` is the authority: it
 * raises `DateRangeError` and the API turns that into a 422
 * `INVALID_DATE_RANGE`. This module exists because §24.11 asks the *screen* to
 * refuse an impossible stay before submitting it, and a form that can only
 * learn "check-out must be after check-in" from a round trip is a bad form.
 *
 * It is deliberately a *second* implementation of one rule, which is normally
 * the thing to avoid. What makes it acceptable is that both sides are bounded
 * by the same number — `stay.max_nights`, fetched from `GET /config` — so the
 * two can only disagree about the *arithmetic*, never about the *policy*. The
 * arithmetic is four lines and is exercised below by the same cases as
 * `test_domain_pricing.py`.
 *
 * `maxNights` is a required keyword with no default, for the same reason
 * `timeZone` is required in `opening-hours.ts`: the wrong default is silently
 * wrong. A `maxNights = 30` here would be NFR-M07's hardcoded business
 * constant, still hardcoded, just further from the eye.
 */

/** A calendar date as `YYYY-MM-DD` — what `<input type="date">` produces. */
export type IsoDate = string;

export type StayCheck =
  | { ok: true; nights: number }
  | { ok: false; reason: 'INCOMPLETE' | 'MALFORMED' | 'NOT_AFTER' | 'TOO_LONG'; message: string };

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const MS_PER_DAY = 86_400_000;

/**
 * Nights between two dates, or why there are none.
 *
 * Returns a result rather than throwing. A form asks this on every keystroke
 * and the "not yet valid" case is the *normal* one while somebody is still
 * typing — exceptions would make the ordinary path the exceptional path.
 */
export function checkStay(
  checkIn: IsoDate | '',
  checkOut: IsoDate | '',
  { maxNights }: { maxNights: number },
): StayCheck {
  if (!checkIn || !checkOut) {
    return { ok: false, reason: 'INCOMPLETE', message: 'Choose both dates.' };
  }
  if (!ISO_DATE.test(checkIn) || !ISO_DATE.test(checkOut)) {
    return { ok: false, reason: 'MALFORMED', message: 'Those dates are not readable.' };
  }

  const nights = nightsBetween(checkIn, checkOut);
  if (Number.isNaN(nights)) {
    return { ok: false, reason: 'MALFORMED', message: 'Those dates are not readable.' };
  }
  if (nights < 1) {
    // BR-101: strictly after. Equal dates are a zero-night stay, which is not
    // a stay — it is the mistake somebody makes on a day trip.
    return { ok: false, reason: 'NOT_AFTER', message: 'Check-out must be after check-in.' };
  }
  if (nights > maxNights) {
    return {
      ok: false,
      reason: 'TOO_LONG',
      message: `A stay can be at most ${maxNights} nights. That one is ${nights}.`,
    };
  }
  return { ok: true, nights };
}

/**
 * Whole days between two calendar dates.
 *
 * Parsed as UTC — which `new Date('2025-08-12')` does, and
 * `new Date(2025, 7, 12)` does not. That is the correct choice here and it is
 * not arbitrary: both operands become midnight in the *same* fixed-offset
 * zone, so their difference is an exact multiple of a day, with no DST
 * transition able to land between them and make a 24-hour subtraction return
 * 23. These are calendar dates, not instants; the arithmetic must not know
 * about clocks at all.
 */
function nightsBetween(checkIn: IsoDate, checkOut: IsoDate): number {
  const from = Date.parse(`${checkIn}T00:00:00Z`);
  const to = Date.parse(`${checkOut}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to)) return Number.NaN;
  return (to - from) / MS_PER_DAY;
}

/** "3 nights", "1 night" — for the summary line. */
export function describeNights(nights: number): string {
  return `${nights} ${nights === 1 ? 'night' : 'nights'}`;
}
