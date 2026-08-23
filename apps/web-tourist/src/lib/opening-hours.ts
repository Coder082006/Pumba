/**
 * Rendering §15.2 opening hours in the destination's timezone.
 *
 * The stored shape is fixed by the SRS:
 *
 *     {
 *       "mon": [["09:00","18:00"]],
 *       "fri": [["09:00","12:00"],["14:00","18:00"]],
 *       "sun": [],
 *       "exceptions": [{ "date": "2027-08-14", "closed": true, "reason": "…" }]
 *     }
 *
 * §15.2 requires it be *"evaluated in the destination's timezone"*, and the
 * backend's `domain/opening_hours.py` enforces that by taking `tz` as a
 * required keyword on every function. This module keeps the same discipline
 * for the same reason: `timeZone` is required here too.
 *
 * The failure it prevents is specific. "Today" resolved against the viewer's
 * clock marks the wrong row for anyone not in the destination's zone — a
 * tourist planning from London at 23:00 would see tomorrow's hours labelled
 * today, and the page would look entirely normal. During Zanzibar-only
 * development, with a server in the same zone, the bug is invisible.
 *
 * This module only *reads* the structure. It does not decide whether a place
 * is open right now — that is `is_open_at` on the server, which has the
 * overnight-range and exception rules and is tested against them. Duplicating
 * that logic here would be two implementations of one rule with nothing
 * pinning them together.
 */

export const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
export type WeekdayKey = (typeof WEEKDAY_KEYS)[number];

export interface OpeningException {
  date: string;
  closed?: boolean;
  reason?: string;
  ranges?: [string, string][];
}

export interface WeekRow {
  key: WeekdayKey;
  /** Localised short name, e.g. "Mon". */
  label: string;
  /** `[["09:00","12:00"],["14:00","18:00"]]`, or `[]` when closed. */
  ranges: [string, string][];
  isToday: boolean;
  /** Present only when an exception replaces the weekly pattern that day. */
  exceptionReason?: string;
}

/** The IANA weekday of `instant` **in `timeZone`** — never the viewer's. */
export function weekdayIn(timeZone: string, instant: Date = new Date()): WeekdayKey {
  const short = new Intl.DateTimeFormat('en-GB', { timeZone, weekday: 'short' }).format(instant);
  const key = short.slice(0, 3).toLowerCase() as WeekdayKey;
  return WEEKDAY_KEYS.includes(key) ? key : 'mon';
}

/** The calendar date in `timeZone`, as `YYYY-MM-DD`, for matching exceptions. */
export function dateIn(timeZone: string, instant: Date = new Date()): string {
  // `en-CA` formats as YYYY-MM-DD, which is the shape the exception list uses.
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(instant);
}

/**
 * Seven rows, starting at today and wrapping — which is what somebody
 * planning a visit reads, rather than a table that always begins on Monday.
 */
export function weekTable(
  openingHours: unknown,
  { timeZone, now = new Date(), locale = 'en-GB' }: { timeZone: string; now?: Date; locale?: string },
): WeekRow[] | null {
  if (openingHours === null || typeof openingHours !== 'object') return null;
  const raw = openingHours as Record<string, unknown>;

  const today = weekdayIn(timeZone, now);
  const todayDate = dateIn(timeZone, now);
  const exceptions = Array.isArray(raw.exceptions) ? (raw.exceptions as OpeningException[]) : [];
  const todayException = exceptions.find((entry) => entry.date === todayDate);

  const start = WEEKDAY_KEYS.indexOf(today);
  const ordered = [...WEEKDAY_KEYS.slice(start), ...WEEKDAY_KEYS.slice(0, start)];

  return ordered.map((key) => {
    const isToday = key === today;
    // An exception outranks the weekly pattern, in both directions: a one-off
    // closure and a one-off opening use the same mechanism.
    const applies = isToday ? todayException : undefined;
    const ranges: [string, string][] = applies
      ? applies.closed
        ? []
        : (applies.ranges ?? [])
      : normaliseRanges(raw[key]);

    const row: WeekRow = { key, label: labelFor(key, locale), ranges, isToday };
    if (applies?.reason) row.exceptionReason = applies.reason;
    return row;
  });
}

function normaliseRanges(value: unknown): [string, string][] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is [string, string] =>
      Array.isArray(entry) && entry.length === 2 && entry.every((t) => typeof t === 'string'),
  );
}

/** A fixed reference week; only the weekday name is taken from it. */
const REFERENCE = {
  mon: Date.UTC(2024, 0, 1),
  tue: Date.UTC(2024, 0, 2),
  wed: Date.UTC(2024, 0, 3),
  thu: Date.UTC(2024, 0, 4),
  fri: Date.UTC(2024, 0, 5),
  sat: Date.UTC(2024, 0, 6),
  sun: Date.UTC(2024, 0, 7),
} as const;

function labelFor(key: WeekdayKey, locale: string): string {
  return new Intl.DateTimeFormat(locale, { weekday: 'short', timeZone: 'UTC' }).format(
    new Date(REFERENCE[key]),
  );
}
