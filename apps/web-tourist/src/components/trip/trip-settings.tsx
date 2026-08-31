'use client';

import { useState } from 'react';

import { updateTrip, type Trip } from '@/lib/trips';

/**
 * Dates and party, after the trip exists — SRS §9.4.2, §41.3.
 *
 * §41.3's outcome is *"a tourist can create a trip, **set dates and party**,
 * …"*, and `/trips/new` only covers the first setting of them. A mistyped
 * arrival date had no fix short of abandoning the trip and starting again,
 * which loses everything already added to it.
 *
 * **Validation is shape only**, the same argument the create form makes: the
 * service owns "a trip cannot start in the past" and `trip.max_days`, and it
 * answers both against the destination's date and a `system_setting`. A copy
 * here would use the device's clock and a hard-coded ceiling.
 *
 * **The destination is not editable.** It fixes the trip's currency (§4.2) and
 * every stored item references a listing beneath it; changing it would leave
 * an itinerary of things in the wrong place, and the API does not accept it on
 * a PATCH either.
 *
 * Collapsed by default. §24.14 calls this screen a workspace for the journey,
 * and a form for facts that rarely change would otherwise sit above the plan
 * itself every time the page is opened.
 */
export function TripSettings({
  trip,
  disabled,
  onSaved,
}: {
  trip: Trip;
  disabled: boolean;
  onSaved: (action: () => Promise<Trip>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [startDate, setStartDate] = useState(trip.start_date);
  const [endDate, setEndDate] = useState(trip.end_date);
  const [adults, setAdults] = useState(String(trip.adults));
  const [children, setChildren] = useState(String(trip.children));
  const [infants, setInfants] = useState(String(trip.infants));
  const [title, setTitle] = useState(trip.title ?? '');

  const field = 'mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm';

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-sm font-medium text-primary hover:underline"
      >
        Edit dates and travellers
      </button>
    );
  }

  return (
    <section aria-labelledby="trip-settings" className="rounded-lg border border-border p-4">
      <h2 id="trip-settings" className="font-display text-lg font-semibold tracking-tight">
        Dates and travellers
      </h2>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="text-sm">
          Arriving
          <input
            type="date"
            className={field}
            value={startDate}
            onChange={(event) => setStartDate(event.target.value)}
          />
        </label>
        <label className="text-sm">
          Leaving
          <input
            type="date"
            className={field}
            value={endDate}
            onChange={(event) => setEndDate(event.target.value)}
          />
        </label>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <label className="text-sm">
          Adults
          <input
            className={field}
            inputMode="numeric"
            value={adults}
            onChange={(event) => setAdults(event.target.value)}
          />
        </label>
        <label className="text-sm">
          Children
          <input
            className={field}
            inputMode="numeric"
            value={children}
            onChange={(event) => setChildren(event.target.value)}
          />
        </label>
        <label className="text-sm">
          Infants
          <input
            className={field}
            inputMode="numeric"
            value={infants}
            onChange={(event) => setInfants(event.target.value)}
          />
        </label>
      </div>

      <label className="mt-4 block text-sm">
        Call it something
        <input
          className={field}
          value={title}
          maxLength={140}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Honeymoon"
        />
      </label>

      <p className="mt-3 text-xs text-muted-foreground">
        Shortening a trip leaves anything now outside it in place — the planner reports it against
        the item so you can move it, rather than deleting something you chose.
      </p>

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          disabled={disabled}
          onClick={() => {
            onSaved(() =>
              updateTrip(trip.public_id, {
                start_date: startDate,
                end_date: endDate,
                adults: Number(adults || '1'),
                children: Number(children || '0'),
                infants: Number(infants || '0'),
                title: title || null,
              }),
            );
            setOpen(false);
          }}
          className="rounded-md bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60"
        >
          Save
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-md border border-border px-5 py-2 text-sm font-semibold transition-colors duration-fast ease-out hover:bg-muted"
        >
          Cancel
        </button>
      </div>
    </section>
  );
}
