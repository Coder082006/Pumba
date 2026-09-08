/**
 * Transfer quoting — SRS §9.3.4, §9.4.4, §12.4.
 *
 * Two public reads and one authenticated quote, which is the whole of API-04
 * as far as a tourist client is concerned. The prices come from the server
 * every time and are never computed here: §18.1 is explicit that "prices are
 * computed server-side, always. The client never sends a price; it sends
 * selections."
 *
 * **The two failures are kept apart on purpose.** §24.16 renders
 * `NO_TARIFF_CONFIGURED` as "transfers to this location are not yet available —
 * contact support" and a routing outage as a retry, and a screen that could not
 * tell them apart would offer a retry that can never succeed. `ApiRequestError`
 * carries the code, so the distinction survives the wire.
 */

import { apiFetch, type ApiRequestError } from './api';
import { authHeaders } from './session';

/** §12.4's class table, as `GET /transport/vehicle-classes` publishes it. */
export interface VehicleClass {
  id: string;
  code: string;
  name: string;
  description: string;
  seats: number;
  luggage_capacity: number;
  has_air_conditioning: boolean;
}

/** A converted figure and the evidence of where it came from (§20.6). */
export interface DisplayMoney {
  amount: string;
  currency: string;
  rate: string;
  as_of: string;
  source: string;
}

export interface Money {
  amount: string;
  currency: string;
  display?: DisplayMoney | null;
}

/** §9.4.4's `breakdown`. The four parts sum to the price exactly. */
export interface FareBreakdown {
  base: string;
  distance: string;
  time: string;
  surcharges: string;
}

/** Which rule priced an option, and on which rung of §12.4's ladder. */
export interface TariffMatch {
  kind: 'CORRIDOR' | 'TARIFF';
  rule: string;
  step: number;
}

export interface FareOption {
  vehicle_class: string;
  seats: number;
  luggage: number;
  price: Money;
  breakdown: FareBreakdown;
  match: TariffMatch;
}

export interface LegQuote {
  reference: string;
  origin: string;
  target: string;
  distance_m: number | null;
  travel_seconds: number | null;
  /** ADR 0019. `APPROXIMATE` is what §24.17's badge is driven by. */
  estimate_quality: string | null;
  polyline: string | null;
  options: FareOption[];
}

/**
 * One end of a leg — §12.2's bindings.
 *
 * Exactly one of the four names a row. `latitude`/`longitude` are a precise
 * pickup *within* it rather than an alternative to it: the place decides which
 * tariff applies and the pin decides where the driver stops, which is why
 * moving a marker changes a metered distance and never re-prices the leg onto
 * a different rung.
 */
export interface LegEndpointInput {
  destination?: string;
  accommodation?: string;
  activity?: string;
  attraction?: string;
  latitude?: string;
  longitude?: string;
}

export interface LegInput {
  reference: string;
  origin: LegEndpointInput;
  target: LegEndpointInput;
  depart_at: string;
  pax: number;
  luggage: number;
}

export function listVehicleClasses(): Promise<VehicleClass[]> {
  return apiFetch<VehicleClass[]>('/transport/vehicle-classes');
}

export interface Corridor {
  id: string;
  origin: { slug: string; name: string };
  target: { slug: string; name: string };
  vehicle_class: string;
  is_bidirectional: boolean;
}

export function listCorridors(): Promise<Corridor[]> {
  return apiFetch<Corridor[]>('/transport/corridors');
}

/**
 * §9.4.4. Prices every leg for every class the party fits.
 *
 * No `Idempotency-Key`, and that is not an omission: §9.1 requires one on a
 * POST that creates a booking, payment or assignment, and this creates none.
 * §9.4.5 is explicit that "transfers hold no inventory but reserve a vehicle
 * class, not a specific driver" — a repeated quote costs nothing and changes
 * nothing, and requiring a key would imply otherwise.
 */
export function quoteTransfers(tripId: string, legs: LegInput[]): Promise<LegQuote[]> {
  return apiFetch<LegQuote[]>('/transport/quotes', {
    method: 'POST',
    headers: authHeaders(),
    body: { trip_id: tripId, legs },
  });
}

/** §24.16's two states, told apart by the code rather than by the message. */
export function isUnconfigured(error: unknown): boolean {
  return (error as ApiRequestError)?.code === 'NO_TARIFF_CONFIGURED';
}

export function isRoutingOutage(error: unknown): boolean {
  return (error as ApiRequestError)?.code === 'ROUTING_UNAVAILABLE';
}
