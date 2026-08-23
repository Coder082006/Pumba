import * as React from 'react';

import { cn } from '../lib/cn';

/**
 * Renders an instant in a destination's timezone — SRS §7.2.
 *
 * §7.2 stores every timestamp as `TIMESTAMPTZ` in UTC and requires it be
 * *"rendered in the destination timezone"*. Not the browser's. A tourist
 * planning a Zanzibar trip from London must see the 08:30 departure as 08:30,
 * and a driver app in the same codebase must see the same instant as the same
 * wall clock. Formatting against the viewer's locale zone would show 05:30 to
 * one of them and nobody would file a bug, because both numbers look
 * plausible.
 *
 * `timeZone` is therefore a **required** prop with no default. There is no
 * correct fallback: omitting it would silently mean "the browser's zone",
 * which is the bug. Every catalogue payload carries `timezone` on its
 * destination for exactly this reason (§7.5.6), so a caller always has one.
 *
 * The rendered text is wrapped in `<time dateTime={...}>` carrying the
 * original UTC instant, so assistive technology and search-engine structured
 * data read the unambiguous value while the human reads the local one.
 */
export interface LocalTimeProps extends Omit<React.TimeHTMLAttributes<HTMLTimeElement>, 'dateTime'> {
  /** An ISO-8601 instant from the API. Must carry an offset or a trailing `Z`. */
  value: string;
  /** IANA zone of the destination this instant belongs to, e.g. `Africa/Dar_es_Salaam`. */
  timeZone: string;
  /** BCP-47 tag. Defaults to the runtime's, which is correct for *language*. */
  locale?: string;
  /**
   * What to show. `date` and `time` are the common cases; `datetime` is for
   * a single instant that needs both, such as an activity departure.
   */
  display?: 'date' | 'time' | 'datetime';
}

const FORMATS: Record<
  NonNullable<LocalTimeProps['display']>,
  Omit<Intl.DateTimeFormatOptions, 'timeZone'>
> = {
  date: { day: 'numeric', month: 'short', year: 'numeric' },
  time: { hour: '2-digit', minute: '2-digit', hour12: false },
  datetime: {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  },
};

export function LocalTime({
  value,
  timeZone,
  locale,
  display = 'datetime',
  className,
  ...props
}: LocalTimeProps) {
  const instant = new Date(value);

  // An unparseable instant renders as the raw string rather than "Invalid
  // Date". The tourist sees something wrong either way, but the raw value is
  // diagnosable and "Invalid Date" is not.
  if (Number.isNaN(instant.getTime())) {
    return (
      <span className={className} {...(props as React.HTMLAttributes<HTMLSpanElement>)}>
        {value}
      </span>
    );
  }

  let formatted: string;
  try {
    formatted = new Intl.DateTimeFormat(locale, { ...FORMATS[display], timeZone }).format(instant);
  } catch {
    // An unknown IANA zone throws. Falling back to UTC — and saying so — beats
    // falling back to the viewer's zone, which would be wrong without looking
    // wrong. The database validates the zone on write (§7.5.6), so this is a
    // corrupted payload rather than an ordinary case.
    formatted = `${new Intl.DateTimeFormat(locale, { ...FORMATS[display], timeZone: 'UTC' }).format(
      instant,
    )} UTC`;
  }

  return (
    <time dateTime={instant.toISOString()} className={cn('tabular-nums', className)} {...props}>
      {formatted}
    </time>
  );
}
