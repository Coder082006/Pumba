'use client';

import { useSyncExternalStore } from 'react';

import { NavLink } from '@/components/shell/nav-link';
import { getPrincipal, subscribeToSession } from '@/lib/session';

/**
 * "Your trips", beside Explore and Where to stay — and only once there are any.
 *
 * It was reachable only from the account menu, behind an unlabelled round
 * button in the corner. A tourist who had just planned a trip could not find
 * the trip again; the link existed and may as well not have. The two things a
 * person does repeatedly in this product are browse and return to their trip,
 * and only one of them was in the nav.
 *
 * **A client island for the reason `AccountMenu` is one.** ADR 0008 keeps the
 * access token in a module variable and never persists it, so no server render
 * can tell a signed-in visitor from an anonymous one. The rest of the header
 * stays on the server; this is the smallest piece that has to know.
 *
 * **Nothing is reserved for it while the session resolves.** `AccountMenu`
 * holds a 36px box open because it always renders *something* and swapping a
 * "Sign in" button for an avatar would jump the row. This link renders nothing
 * at all when signed out, so a placeholder would create the gap it was meant to
 * prevent — a blank space on every anonymous page load, for a link that is
 * never coming.
 *
 * It does not call `ensureSession`. `AccountMenu` sits in this same header and
 * already does, and `/auth/refresh` rotates: a second unguarded refresh would
 * present a superseded token, which the server treats as theft and answers by
 * revoking the family and emailing the owner. Subscribing is enough — the
 * store publishes when that one bootstrap lands.
 */
export function TripsNavLink() {
  const principal = useSyncExternalStore(subscribeToSession, getPrincipal, () => null);

  if (principal === null) return null;
  return <NavLink href="/trips">Your trips</NavLink>;
}
