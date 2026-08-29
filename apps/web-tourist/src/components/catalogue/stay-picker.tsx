'use client';

import { useMemo, useState, type ReactNode } from 'react';

import { checkStay, describeNights, type StayCheck } from '@/lib/stay';
import type { Accommodation } from '@pumba/contracts';

/**
 * "Where are you staying" — SRS §24.11 as amended (ADR 0013).
 *
 * ## What this screen can and cannot do in Phase 3
 *
 * §24.11 ends at `POST /trips/{id}/items` with `item_type` `STAY`. There is no
 * such route: `apps/api/apps/trip/` is still the Phase 1 skeleton — seven
 * lines of docstring, no `trip`, no `itinerary`, no `itinerary_item` — so
 * **nothing on this screen can be saved yet, by either path**. That is not a
 * property of the free-entry path; the curated path cannot save either.
 *
 * So this is the *selection* surface, and the submit is disabled with its
 * reason stated — the same treatment the Activity page gives "Add to trip",
 * for the same missing module. Everything §24.11 specifies short of the POST
 * is real and is here: the curated list with exact seeded coordinates, the
 * search over it, the map, the dates with BR-101 enforced, and the
 * "I haven't booked yet" branch.
 *
 * ## Free entry captures text, and no coordinate
 *
 * §24.11 wants free entry resolved through the §13.2 geocoding path and shown
 * as a pin the tourist confirms. `apps/api/apps/location/` is also a skeleton:
 * there is no `RoutingPort`, no geocoder, not even a fake one. Appendix D2 is
 * the decision that unblocks it.
 *
 * The tempting move — put a pin *somewhere* and let the tourist press
 * "confirm" — is the one thing §13.2 forbids, and confirmation is what makes
 * it worse rather than better: it launders a coordinate the Platform invented
 * into one the tourist appears to have vouched for. A transfer would then
 * quote from it, to the metre, with total confidence.
 *
 * So free entry takes the name or address as **text only**, and the screen
 * says plainly that an unplaced stay cannot have transfers planned around it —
 * which is VR-16's warning, arriving at the moment the tourist can still do
 * something about it rather than later in the itinerary. The pin returns with
 * D2, at which point this branch gains a map and a real confirmation step.
 *
 * §24.11's "geocode-failed → ask the tourist to drop the pin themselves" is
 * not built either. A hand-dropped pin is not a fabrication — it is exactly
 * the human confirmation §13.2 asks for — but building the *fallback* for a
 * path whose primary half does not exist would invert the screen, and its
 * only consumer is the POST that is also missing.
 */

export interface StayPickerProps {
  /** The destination's curated properties — seeded, with exact coordinates. */
  properties: Accommodation[];
  destinationName: string;
  /**
   * `stay.max_nights`, from `GET /config`. Never a literal.
   *
   * `null` means the config fetch failed. Typed rather than defaulted: a
   * fallback of 30 would put the constant back in the front end, and would do
   * it silently on exactly the day the row had been changed. The date fields
   * disable and say so instead — the list and the map are unaffected, so an
   * outage costs the dates rather than the page.
   */
  maxNights: number | null;
  /** The map, rendered on the server and passed in as a slot. */
  map: ReactNode;
}

type Selection =
  | { kind: 'none' }
  | { kind: 'curated'; property: Accommodation }
  | { kind: 'free'; name: string }
  | { kind: 'not-booked' };

export function StayPicker({ properties, destinationName, maxNights, map }: StayPickerProps) {
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState<Selection>({ kind: 'none' });
  const [freeText, setFreeText] = useState('');
  const [checkIn, setCheckIn] = useState('');
  const [checkOut, setCheckOut] = useState('');

  const matches = useMemo(() => filterProperties(properties, query), [properties, query]);
  const stay: StayCheck =
    maxNights === null
      ? { ok: false, reason: 'INCOMPLETE', message: '' }
      : checkStay(checkIn, checkOut, { maxNights });
  const showDateError = stay.ok === false && stay.reason !== 'INCOMPLETE';

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div className="space-y-3">
        {map}
        <p className="text-xs text-muted-foreground">
          Pins show the {properties.length === 1 ? 'property' : 'properties'} we already know in{' '}
          {destinationName}.
        </p>
      </div>

      <div className="space-y-6">
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Dates</legend>
          <div className="flex flex-wrap gap-4">
            <label className="text-sm">
              <span className="block text-muted-foreground">Check in</span>
              <input
                type="date"
                value={checkIn}
                disabled={maxNights === null}
                onChange={(event) => setCheckIn(event.target.value)}
                className="mt-1 rounded-md border border-border px-2 py-1 disabled:bg-muted"
              />
            </label>
            <label className="text-sm">
              <span className="block text-muted-foreground">Check out</span>
              <input
                type="date"
                value={checkOut}
                disabled={maxNights === null}
                onChange={(event) => setCheckOut(event.target.value)}
                className="mt-1 rounded-md border border-border px-2 py-1 disabled:bg-muted"
              />
            </label>
          </div>
          {maxNights === null ? (
            <p role="status" className="text-sm text-warning-foreground">
              Dates are temporarily unavailable — we can&rsquo;t reach the settings that bound how
              long a stay can be. The properties below are still browsable.
            </p>
          ) : null}
          {/* Reserved whether or not there is a message, so typing a date does
              not push the list below it down the page (§29, CLS). */}
          <p
            role={showDateError ? 'alert' : undefined}
            className={`min-h-[1.25rem] text-sm ${showDateError ? 'text-destructive-ink' : 'text-muted-foreground'}`}
          >
            {stay.ok ? describeNights(stay.nights) : showDateError ? stay.message : ''}
          </p>
        </fieldset>

        <section aria-labelledby="curated" className="space-y-2">
          <h2 id="curated" className="text-sm font-medium">
            Properties in {destinationName}
          </h2>
          <label className="block text-sm">
            <span className="sr-only">Search {destinationName} properties</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={`Search ${destinationName} properties…`}
              className="w-full rounded-md border border-border px-3 py-2"
            />
          </label>

          {matches.length > 0 ? (
            <ul className="divide-y divide-border rounded-md border border-border">
              {matches.map((property) => (
                <li key={property.public_id}>
                  <button
                    type="button"
                    onClick={() => setSelection({ kind: 'curated', property })}
                    aria-pressed={
                      selection.kind === 'curated' &&
                      selection.property.public_id === property.public_id
                    }
                    className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-muted aria-pressed:bg-muted"
                  >
                    <span className="font-medium">{property.name}</span>
                    <span className="shrink-0 text-muted-foreground">
                      {property.property_type} · {property.destination.name}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            // §24.11: "a no-match state that offers free entry rather than a
            // dead end". An empty list with nothing after it is the dead end.
            <p className="rounded-md border border-dashed border-border bg-muted p-3 text-sm text-muted-foreground">
              {properties.length === 0
                ? `We do not have any properties listed in ${destinationName} yet.`
                : `Nothing matches “${query}”.`}{' '}
              You can still type where you are staying below.
            </p>
          )}
        </section>

        <section aria-labelledby="free-entry" className="space-y-2">
          <h2 id="free-entry" className="text-sm font-medium">
            Can&rsquo;t find it?
          </h2>
          <label className="block text-sm">
            <span className="sr-only">Hotel name or address</span>
            <input
              type="text"
              value={freeText}
              onChange={(event) => {
                setFreeText(event.target.value);
                setSelection(
                  event.target.value.trim()
                    ? { kind: 'free', name: event.target.value.trim() }
                    : { kind: 'none' },
                );
              }}
              placeholder="Enter any hotel name or address"
              className="w-full rounded-md border border-border px-3 py-2"
              aria-describedby="free-entry-note"
            />
          </label>
          <p
            id="free-entry-note"
            className="rounded-md border border-warning-border bg-warning p-3 text-sm text-warning-foreground"
          >
            We record this by name. We can&rsquo;t place it on the map yet, so we won&rsquo;t be
            able to plan your airport transfers or day trips around it — pick it from the list
            above if it is there.
          </p>
        </section>

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={selection.kind === 'not-booked'}
            onChange={(event) =>
              setSelection(event.target.checked ? { kind: 'not-booked' } : { kind: 'none' })
            }
            className="mt-1"
          />
          <span>
            I haven&rsquo;t booked anywhere yet
            {/* VR-16 is a warning, not a block (§10.6). Saying so here keeps
                the option from feeling like an admission of failure. */}
            <span className="block text-muted-foreground">
              That&rsquo;s fine — you can still plan the days. We&rsquo;ll flag any night with
              nowhere to stay so you can come back to it.
            </span>
          </span>
        </label>

        <Summary selection={selection} stay={stay} />
      </div>
    </div>
  );
}

function Summary({ selection, stay }: { selection: Selection; stay: StayCheck }) {
  const ready = stay.ok && selection.kind !== 'none';

  return (
    <div className="space-y-2 border-t border-border pt-4">
      <p className="text-sm" aria-live="polite">
        {describeSelection(selection, stay)}
      </p>
      <button
        type="button"
        disabled
        className="w-full cursor-not-allowed rounded-md border border-border bg-muted px-4 py-2 text-sm text-muted-foreground"
      >
        {ready ? 'Add to trip — coming soon' : 'Add to trip'}
      </button>
      {/* The honest version of a disabled button. Without this it reads as a
          bug, or as the form having silently rejected what was entered. */}
      <p className="text-xs text-muted-foreground">
        Saving a stay arrives with the trip planner. Nothing on this page is stored yet.
      </p>
    </div>
  );
}

function describeSelection(selection: Selection, stay: StayCheck): string {
  const nights = stay.ok ? ` for ${describeNights(stay.nights)}` : '';
  switch (selection.kind) {
    case 'curated':
      return `${selection.property.name}${nights}.`;
    case 'free':
      return `${selection.name}${nights} — recorded by name, not placed on the map.`;
    case 'not-booked':
      return 'No accommodation recorded. Nights without a stay will be flagged.';
    case 'none':
      return 'Pick a property, or type where you are staying.';
  }
}

/**
 * Filtered in the browser over the page already fetched, not by a round trip.
 *
 * §24.11 asks for "a search field over the curated accommodation list for this
 * destination" — a filter over a known, small set, which is a different thing
 * from `/search`. One destination's properties fit in one page, so a server
 * round trip per keystroke would add latency and a failure mode to a list that
 * is already on the client.
 */
function filterProperties(properties: Accommodation[], query: string): Accommodation[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return properties;
  return properties.filter((property) =>
    `${property.name} ${property.address_line} ${property.destination.name}`
      .toLowerCase()
      .includes(needle),
  );
}
