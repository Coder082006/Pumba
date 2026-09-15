/**
 * My Trips' four segments — SRS §24.24.
 *
 *   Segmented control (Upcoming, Active, Past, Drafts).
 *
 * Pure, so the rule is tested once rather than read off a page. The question
 * each segment answers is the tourist's, not the state machine's:
 *
 * - **Drafts** — not yet reserved: a plan, a priced quote, or a basket still
 *   waiting for payment. Everything a tourist can still change or abandon.
 * - **Active** — happening now: in progress, or confirmed and inside its dates.
 * - **Upcoming** — confirmed and still ahead.
 * - **Past** — over, whether it happened or was cancelled.
 *
 * "Today" is a date string in the trip's own terms, passed in, so the rule has
 * no clock of its own and a test can stand on any day.
 */

export type Segment = 'UPCOMING' | 'ACTIVE' | 'PAST' | 'DRAFTS';

export const SEGMENTS: readonly Segment[] = ['UPCOMING', 'ACTIVE', 'PAST', 'DRAFTS'];

export const SEGMENT_LABEL: Record<Segment, string> = {
  UPCOMING: 'Upcoming',
  ACTIVE: 'Active',
  PAST: 'Past',
  DRAFTS: 'Drafts',
};

interface Segmentable {
  status: string;
  start_date: string;
  end_date: string;
}

const DRAFT_STATES = new Set(['DRAFT', 'PRICED', 'PENDING_PAYMENT']);
const OVER_STATES = new Set(['COMPLETED', 'CANCELLED']);

export function segmentOf(trip: Segmentable, today: string): Segment {
  if (DRAFT_STATES.has(trip.status)) return 'DRAFTS';
  if (OVER_STATES.has(trip.status)) return 'PAST';
  if (trip.status === 'IN_PROGRESS') return 'ACTIVE';
  // CONFIRMED: by its dates. ISO dates compare correctly as strings.
  if (trip.end_date < today) return 'PAST';
  if (trip.start_date <= today) return 'ACTIVE';
  return 'UPCOMING';
}

export function tripStatusLabel(status: string): string {
  switch (status) {
    case 'DRAFT':
      return 'Planning';
    case 'PRICED':
      return 'Priced';
    case 'PENDING_PAYMENT':
      return 'Reserved, awaiting payment';
    case 'CONFIRMED':
      return 'Confirmed';
    case 'IN_PROGRESS':
      return 'Under way';
    case 'COMPLETED':
      return 'Completed';
    case 'CANCELLED':
      return 'Cancelled';
    default:
      return status;
  }
}

export const EMPTY_SEGMENT: Record<Segment, string> = {
  UPCOMING: 'Nothing confirmed ahead yet.',
  ACTIVE: 'No trip is under way right now.',
  PAST: 'No finished trips yet.',
  DRAFTS: 'No trips in planning.',
};
