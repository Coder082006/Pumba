import type { Metadata } from 'next';

import { RequireSignIn } from '@/components/auth/require-sign-in';

/**
 * Everything under `/trips` belongs to one person — SRS §24.14, §30.3.
 *
 * A layout rather than a check repeated in each page: `/trips`, `/trips/new`,
 * the planner, the flights screen, the itinerary and the summary all need the
 * same answer, and six copies of a guard is five chances to forget one.
 *
 * Next.js middleware cannot do this. ADR 0008 keeps the access token in the
 * browser's memory and the refresh cookie is HttpOnly and scoped to
 * `/api/v1/auth`, so nothing on the Next server can tell a signed-in request
 * from a signed-out one. The decision has to be made in the browser, which is
 * also why this is a convenience rather than the control — the API is what
 * actually refuses, by owner, with a 404.
 *
 * `noindex` because these are private workspaces. `robots.ts` already
 * disallows the path; this says the same thing to a crawler that arrived by a
 * link rather than by the robots file.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function TripsLayout({ children }: { children: React.ReactNode }) {
  return <RequireSignIn>{children}</RequireSignIn>;
}
