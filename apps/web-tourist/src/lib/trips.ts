/**
 * The trip API, from the browser — SRS §9.4.2, §24.14.
 *
 * **Every call here runs client-side, and that is forced rather than chosen.**
 * `lib/session` holds the access token in a module variable and never persists
 * it (ADR 0008), so a Server Component has no way to authenticate: there is no
 * cookie a server render could read. The catalogue pages stay server-rendered
 * because §24.8 makes them an SEO surface; a trip is nobody's search result,
 * and §24.14 calls it a workspace.
 *
 * The consequence worth stating: these screens render nothing useful without
 * JavaScript. That is the right trade for a private, interactive workspace and
 * the wrong one for a destination page, which is why the two are built
 * differently.
 *
 * Types come from `@pumba/contracts`, generated from the committed OpenAPI
 * document. Hand-written shapes here would be a second description of the same
 * API that drifts the first time a field is added.
 */

import { apiFetch, type RequestOptions } from '@/lib/api';
import { authHeaders } from '@/lib/session';

import type { components } from '@pumba/contracts';

export type Trip = components['schemas']['Trip'];
export type TripSummary = components['schemas']['TripSummary'];
export type ItineraryItem = components['schemas']['ItineraryItem'];
export type Finding = components['schemas']['Finding'];
export type TripFlight = components['schemas']['TripFlight'];

/** `apiFetch` with the bearer token attached. */
function authed<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return apiFetch<T>(path, {
    ...options,
    headers: { ...authHeaders(), ...options.headers },
  });
}

export function listTrips(): Promise<TripSummary[]> {
  return authed<TripSummary[]>('/trips');
}

export function getTrip(publicId: string): Promise<Trip> {
  return authed<Trip>(`/trips/${publicId}`);
}

export interface CreateTripInput {
  destination: string;
  start_date: string;
  end_date: string;
  adults?: number;
  children?: number;
  infants?: number;
  title?: string | null;
}

export function createTrip(input: CreateTripInput): Promise<Trip> {
  return authed<Trip>('/trips', { method: 'POST', body: input });
}

export function updateTrip(publicId: string, changes: Partial<CreateTripInput>): Promise<Trip> {
  return authed<Trip>(`/trips/${publicId}`, { method: 'PATCH', body: changes });
}

/**
 * `POST /trips/{id}/items`.
 *
 * The listing is **named**, by the slug or UUID that is already in the address
 * bar of the page the tourist is adding from. It took the catalogue row's
 * integer primary key until Phase 4's last mile, which made the endpoint
 * uncallable from here: §7.2 keeps sequential integers inside the database, so
 * nothing this client can see would have satisfied it.
 *
 * `title` is deliberately absent. The server titles the item from the listing's
 * own name, so two tourists adding the same activity get the same words — and a
 * title this client invented would end up in an emailed confirmation. FREE_TIME
 * is the exception and the API takes a title for it; nothing here adds one yet.
 */
export interface AddItemInput {
  item_type: 'STAY' | 'ACTIVITY' | 'ATTRACTION';
  day_number: number;
  sequence_no: number;
  starts_at: string;
  ends_at: string;
  accommodation?: string;
  activity?: string;
  attraction?: string;
}

export function addItem(publicId: string, item: AddItemInput): Promise<Trip> {
  return authed<Trip>(`/trips/${publicId}/items`, { method: 'POST', body: item });
}

export function removeItem(publicId: string, itemId: string): Promise<Trip> {
  return authed<Trip>(`/trips/${publicId}/items/${itemId}`, { method: 'DELETE' });
}

export function setFlights(publicId: string, flights: unknown[]): Promise<Trip> {
  return authed<Trip>(`/trips/${publicId}/flights`, { method: 'PUT', body: { flights } });
}

export function generateItinerary(publicId: string): Promise<Trip> {
  return authed<Trip>(`/trips/${publicId}/itinerary/generate`, { method: 'POST' });
}

export function cancelTrip(publicId: string): Promise<Trip> {
  return authed<Trip>(`/trips/${publicId}/cancel`, { method: 'POST' });
}

/**
 * Findings that name a given item — §10.6, §24.14.
 *
 * §10.6 gives every finding `item_ids` *"so the client can render an inline
 * fix affordance"*. A banner at the top of the page listing "3 problems" is the
 * version people learn to dismiss; the point of the field is that the warning
 * appears against the row it is about.
 */
export function findingsFor(item: ItineraryItem, findings: readonly Finding[]): Finding[] {
  return findings.filter((finding) => finding.item_ids?.includes(item.public_id));
}

/**
 * Findings that name no item at all.
 *
 * VR-16 is the example: it is about nights that have *no* stay, so there is
 * nothing to anchor it to. These are the only ones that legitimately belong in
 * a banner.
 */
export function tripLevelFindings(findings: readonly Finding[]): Finding[] {
  return findings.filter((finding) => (finding.item_ids?.length ?? 0) === 0);
}

/** Items grouped by day, in the order the planner put them. */
export function byDay(items: readonly ItineraryItem[]): Map<number, ItineraryItem[]> {
  const days = new Map<number, ItineraryItem[]>();
  for (const item of items) {
    const day = days.get(item.day_number) ?? [];
    day.push(item);
    days.set(item.day_number, day);
  }
  for (const day of days.values()) {
    day.sort((a, b) => a.sequence_no - b.sequence_no);
  }
  return new Map([...days.entries()].sort(([a], [b]) => a - b));
}
