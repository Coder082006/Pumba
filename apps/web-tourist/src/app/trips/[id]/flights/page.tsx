'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useState } from 'react';

import { ApiRequestError } from '@/lib/api';
import { getTrip, setFlights, type Trip } from '@/lib/trips';

/**
 * Flight Information — SRS §24.15.
 *
 * *"Capture arrival and departure details so transfers can be timed."* Two
 * panels, and a note explaining the buffer, because the buffer is the reason
 * the screen exists: §11.3 times the airport pickup from the arrival plus
 * `buffer.arrival_processing_minutes`, and a tourist who does not know that is
 * a tourist who thinks the platform has booked their driver too late.
 *
 * **Validation here is shape only.** §24.15 gives a flight-number pattern and
 * a set of range rules; the ranges — arrival within the trip, departure after
 * arrival — are VR-07, VR-08 and VR-01 server-side, and duplicating them in the
 * browser would be a second copy that disagrees the first time a buffer setting
 * changes. So the form checks what a field *looks* like and lets §10.6 answer
 * what it *means*.
 *
 * **The gateway is any destination, not only a flagged one.** §7.5.6 has an
 * `is_gateway` flag, and `services.set_flights` deliberately does not require
 * it: a destination the tourist believes they are flying into is a fact about
 * their trip, and refusing it because the catalogue has not been flagged would
 * surface our bookkeeping as their problem.
 */

const AIRLINE = /^[A-Z0-9]{2}$/;
const FLIGHT_NUMBER = /^\d{1,4}$/;

interface FlightForm {
  gateway: string;
  flight_number: string;
  airline_iata: string;
  scheduled_at: string;
  pax_count: string;
  luggage_count: string;
}

const EMPTY: FlightForm = {
  gateway: '',
  flight_number: '',
  airline_iata: '',
  scheduled_at: '',
  pax_count: '',
  luggage_count: '0',
};

function problemsIn(form: FlightForm): string[] {
  if (!form.gateway && !form.flight_number && !form.airline_iata && !form.scheduled_at) {
    return []; // An untouched panel is not an error; it is "no such flight".
  }
  const problems: string[] = [];
  if (!AIRLINE.test(form.airline_iata.toUpperCase())) {
    problems.push('The airline code is two letters or digits, such as TC or KL.');
  }
  if (!FLIGHT_NUMBER.test(form.flight_number)) {
    problems.push('The flight number is up to four digits.');
  }
  if (!form.scheduled_at) problems.push('The scheduled time is needed to plan the transfer.');
  if (!form.gateway) problems.push('Choose the airport you are flying into or out of.');
  if (form.pax_count && Number(form.pax_count) < 1) {
    problems.push('At least one passenger.');
  }
  return problems;
}

function isFilled(form: FlightForm): boolean {
  return Boolean(form.gateway && form.flight_number && form.airline_iata && form.scheduled_at);
}

function Panel({
  legend,
  form,
  onChange,
  destinations,
}: {
  legend: string;
  form: FlightForm;
  onChange: (next: FlightForm) => void;
  destinations: { slug: string; name: string }[];
}) {
  const problems = problemsIn(form);
  const field = 'mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm';

  return (
    <fieldset className="rounded-lg border border-border p-4 sm:p-6">
      <legend className="px-2 font-display text-lg font-semibold tracking-tight">{legend}</legend>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm">
          Airline code
          <input
            className={field}
            value={form.airline_iata}
            maxLength={3}
            onChange={(e) => onChange({ ...form, airline_iata: e.target.value.toUpperCase() })}
            placeholder="TC"
          />
        </label>
        <label className="text-sm">
          Flight number
          <input
            className={field}
            value={form.flight_number}
            maxLength={4}
            inputMode="numeric"
            onChange={(e) => onChange({ ...form, flight_number: e.target.value })}
            placeholder="451"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Scheduled time
          <input
            type="datetime-local"
            className={field}
            value={form.scheduled_at}
            onChange={(e) => onChange({ ...form, scheduled_at: e.target.value })}
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Airport
          <select
            className={field}
            value={form.gateway}
            onChange={(e) => onChange({ ...form, gateway: e.target.value })}
          >
            <option value="">Choose…</option>
            {destinations.map((d) => (
              <option key={d.slug} value={d.slug}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          Passengers
          <input
            className={field}
            value={form.pax_count}
            inputMode="numeric"
            onChange={(e) => onChange({ ...form, pax_count: e.target.value })}
          />
        </label>
        <label className="text-sm">
          Large bags
          <input
            className={field}
            value={form.luggage_count}
            inputMode="numeric"
            onChange={(e) => onChange({ ...form, luggage_count: e.target.value })}
          />
        </label>
      </div>

      {problems.length > 0 ? (
        <ul className="mt-4 space-y-1 text-sm text-destructive-ink">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}

export default function FlightInformationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [trip, setTrip] = useState<Trip | null>(null);
  const [destinations, setDestinations] = useState<{ slug: string; name: string }[]>([]);
  const [inbound, setInbound] = useState<FlightForm>(EMPTY);
  const [outbound, setOutbound] = useState<FlightForm>(EMPTY);
  const [saved, setSaved] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await getTrip(id);
        setTrip(loaded);
        setInbound((f) => ({ ...f, pax_count: String(loaded.adults + loaded.children) }));
        setOutbound((f) => ({ ...f, pax_count: String(loaded.adults + loaded.children) }));
        // Only the trip's own destination is offered. A gateway picker over
        // the whole catalogue would let somebody record a flight into a place
        // their trip is not, which VR-07 could then never make sense of.
        setDestinations([{ slug: loaded.destination.slug, name: loaded.destination.name }]);
      } catch (error) {
        setProblem(
          error instanceof ApiRequestError && error.status === 404
            ? 'That trip could not be found.'
            : 'This trip could not be loaded just now.',
        );
      }
    })();
  }, [id]);

  const save = useCallback(async () => {
    setBusy(true);
    setProblem(null);
    setSaved(false);
    const payload = [
      ...(isFilled(inbound) ? [{ ...toPayload(inbound), direction: 'INBOUND' }] : []),
      ...(isFilled(outbound) ? [{ ...toPayload(outbound), direction: 'OUTBOUND' }] : []),
    ];
    try {
      setTrip(await setFlights(id, payload));
      setSaved(true);
    } catch (error) {
      setProblem(
        error instanceof ApiRequestError ? error.message : 'Those flights could not be saved.',
      );
    } finally {
      setBusy(false);
    }
  }, [id, inbound, outbound]);

  const blocked =
    problemsIn(inbound).length > 0 || problemsIn(outbound).length > 0 || busy || trip === null;

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <p className="text-sm text-muted-foreground">
          <Link href={`/trips/${id}`} className="hover:underline">
            Back to the planner
          </Link>
        </p>
        <h1 className="font-display text-3xl font-bold tracking-tight">Your flights</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          These are what the airport transfer is timed from. Your pickup is scheduled at least{' '}
          <strong className="font-medium text-foreground">45 minutes</strong> after you land, so
          there is time for immigration, bags and customs — you will not be waiting for a driver
          who has already gone.
        </p>
      </header>

      {problem ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive-ink">
          {problem}
        </p>
      ) : null}
      {saved ? (
        <p className="rounded-md border border-border bg-accent/10 px-3 py-2 text-sm">
          Saved. Plan the days again to re-time the transfers around them.
        </p>
      ) : null}

      <Panel legend="Arriving" form={inbound} onChange={setInbound} destinations={destinations} />
      <Panel
        legend="Leaving"
        form={outbound}
        onChange={setOutbound}
        destinations={destinations}
      />

      <button
        type="button"
        disabled={blocked}
        onClick={() => void save()}
        className="rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60"
      >
        {busy ? 'Saving…' : 'Save flights'}
      </button>
    </div>
  );
}

function toPayload(form: FlightForm): Record<string, unknown> {
  return {
    gateway: form.gateway,
    airline_iata: form.airline_iata.toUpperCase(),
    flight_number: form.flight_number,
    // `datetime-local` yields a naive string. The server stores TIMESTAMPTZ in
    // UTC (§7.2), so the browser's offset is applied here rather than leaving
    // the API to guess which zone a bare wall time belongs to.
    scheduled_at: new Date(form.scheduled_at).toISOString(),
    pax_count: Number(form.pax_count || '1'),
    luggage_count: Number(form.luggage_count || '0'),
  };
}
