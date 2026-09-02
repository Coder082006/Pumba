'use client';

import { useEffect, useMemo, useState } from 'react';
import { LocalTime } from '@pumba/ui';

import {
  byLocalDate,
  listDepartures,
  scarcity,
  unbookableLabel,
  type Departure,
} from '@/lib/inventory';

/**
 * An activity's real departures — SRS §24.10, §16.2, §17.1.
 *
 * This replaces the panel that said *"Live departure dates are not available
 * yet"*, which was true from Phase 3 until Phase 5 materialised the calendar.
 *
 * **Three things it must not do, and they are the whole design.**
 *
 * *It must not promise a seat.* §17.1 I3: search may read stale capacity;
 * committing a booking may not. The remaining counts here came from a
 * sixty-second cache, and the only authoritative number in the system is taken
 * under a row lock inside the quote. So the copy says "held when you ask for a
 * price", the counts are coarse (`scarcity`), and nothing here is phrased as a
 * reservation.
 *
 * *It must not hide a date.* A cancelled or sold-out departure is shown,
 * labelled, and unselectable. A calendar that silently omitted them would read
 * as a bug to somebody who was looking at that date a minute ago — and a
 * tourist deciding between two weeks needs to see which one is full.
 *
 * *It must not group by the browser's zone.* An 08:30 Zanzibar departure is
 * 05:30 in London; grouping on the viewer's clock would file it under the
 * previous day for anybody west of the destination, and the tourist would book
 * the wrong date on a correct calendar.
 *
 * **Selection is lifted, not owned.** The picker reports the departure's exact
 * instant and the parent sends that as the item's `starts_at` — which is how a
 * departure is bound at quote time without any client knowing an internal id
 * (ADR 0022).
 */

export interface DeparturePickerProps {
  /** Slug or UUID — whatever addressed the activity page itself. */
  reference: string;
  /** The destination's IANA zone. Departures are grouped and rendered in it. */
  timeZone: string;
  /** Party size, if known. Turns the list into advice rather than a listing. */
  pax?: number | undefined;
  /** The currently chosen departure's `departs_at`, or empty. */
  value: string;
  onChange: (departsAt: string) => void;
}

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; departures: Departure[] }
  | { kind: 'empty' }
  | { kind: 'error' };

/** §24.10 shows a month. Longer is a calendar nobody scrolls. */
const WINDOW_DAYS = 30;

export function DeparturePicker({
  reference,
  timeZone,
  pax,
  value,
  onChange,
}: DeparturePickerProps) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const departures = await listDepartures(reference, { pax });
        if (!live) return;
        setState(departures.length === 0 ? { kind: 'empty' } : { kind: 'ready', departures });
      } catch {
        if (live) setState({ kind: 'error' });
      }
    })();
    return () => {
      // The party size can change while a request is in flight — the tourist
      // picks a different trip in the panel below. Without this, a slow reply
      // for the old size lands after the fast one for the new.
      live = false;
    };
  }, [reference, pax]);

  const days = useMemo(
    () => (state.kind === 'ready' ? byLocalDate(state.departures, timeZone) : new Map()),
    [state, timeZone],
  );

  if (state.kind === 'loading') {
    // Roughly what replaces it — §29 measures CLS and this sits above the add
    // panel, so a shift here moves the button somebody is reaching for.
    return <div aria-hidden className="h-48 rounded-md bg-muted" />;
  }

  if (state.kind === 'error') {
    return (
      <div className="rounded-md border border-border bg-muted p-3 text-sm text-muted-foreground">
        <p className="font-medium text-foreground">Dates and availability</p>
        <p className="mt-1">
          Departure dates could not be loaded just now. You can still add this to a trip and
          choose a time.
        </p>
      </div>
    );
  }

  if (state.kind === 'empty') {
    return (
      <div className="rounded-md border border-dashed border-border bg-muted p-3 text-sm text-muted-foreground">
        <p className="font-medium text-foreground">Dates and availability</p>
        <p className="mt-1">
          No departures are published for the next {WINDOW_DAYS} days. The operator sets these,
          and they are usually added a season at a time.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium">Choose a departure</h3>
        <p className="text-xs text-muted-foreground">Next {WINDOW_DAYS} days</p>
      </div>

      <ul className="mt-3 max-h-72 space-y-3 overflow-y-auto pr-1">
        {[...days.entries()].map(([date, entries]) => (
          <li key={date}>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {formatDay(date, timeZone)}
            </p>
            <div className="mt-1 flex flex-wrap gap-2">
              {(entries as Departure[]).map((departure) => (
                <DepartureButton
                  key={departure.public_id}
                  departure={departure}
                  timeZone={timeZone}
                  selected={departure.departs_at === value}
                  onChange={onChange}
                />
              ))}
            </div>
          </li>
        ))}
      </ul>

      <p className="mt-3 text-xs text-muted-foreground">
        Seats shown are indicative. They are held for you when you ask for a price, not when you
        add this to a trip.
      </p>
    </div>
  );
}

function DepartureButton({
  departure,
  timeZone,
  selected,
  onChange,
}: {
  departure: Departure;
  timeZone: string;
  selected: boolean;
  onChange: (departsAt: string) => void;
}) {
  const reason = unbookableLabel(departure.unbookable);
  const disabled = !departure.is_bookable;
  const left = scarcity(departure.remaining);

  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={selected}
      onClick={() => onChange(departure.departs_at)}
      className={[
        'rounded-md border px-3 py-2 text-left text-sm transition-colors duration-fast ease-out',
        selected ? 'border-primary bg-primary/10' : 'border-border',
        disabled ? 'cursor-not-allowed opacity-60' : 'hover:bg-muted',
      ].join(' ')}
    >
      <span className="block font-medium">
        <LocalTime value={departure.departs_at} timeZone={timeZone} display="time" />
      </span>
      <span className="block text-xs text-muted-foreground">
        {reason ?? (left === 'few' ? `Only ${departure.remaining} left` : 'Seats available')}
      </span>
    </button>
  );
}

/** The date, spelled out, in the destination's zone rather than the viewer's. */
function formatDay(isoDate: string, timeZone: string): string {
  // `isoDate` is already the local date at the destination; parsing it as UTC
  // noon and rendering it back in the same zone keeps it there whatever the
  // viewer's offset is.
  const at = new Date(`${isoDate}T12:00:00Z`);
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  }).format(at);
}
