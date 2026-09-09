/**
 * "Your trips" in the header nav — SRS §24, ADR 0008.
 *
 * The link existed only inside the account menu, behind an unlabelled round
 * button, and a tourist who had just planned a trip could not find it again.
 * Moving it into the nav is only useful if it appears for the right people at
 * the right moment, which is what these three cover:
 *
 *   1. Nothing at all when signed out. A "Your trips" link that bounces an
 *      anonymous visitor to the sign-in page is worse than no link.
 *   2. The link when signed in.
 *   3. It has to notice a session that begins *after* it mounted — the header
 *      lives in the layout, which the App Router does not remount on a
 *      client-side navigation, so signing in and being pushed to `/` would
 *      otherwise leave the nav without it until a full reload.
 */

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { TripsNavLink } from '@/components/shell/trips-nav-link';
import { clearSession, setPrincipal } from '@/lib/session';

const ADA = { publicId: 'a-uuid', roles: ['TOURIST'] };

beforeEach(() => {
  clearSession();
});

afterEach(() => {
  clearSession();
});

describe('the trips nav link', () => {
  it('shows nothing to a signed-out visitor', () => {
    const { container } = render(<TripsNavLink />);

    expect(screen.queryByRole('link', { name: 'Your trips' })).toBeNull();
    // Not merely hidden: an empty island reserving width would open a gap in
    // the nav on every anonymous page load, for a link that never arrives.
    expect(container.innerHTML).toBe('');
  });

  it('shows the link to a signed-in tourist', async () => {
    setPrincipal(ADA);

    render(<TripsNavLink />);

    const link = await screen.findByRole('link', { name: 'Your trips' });
    expect(link.getAttribute('href')).toBe('/trips');
  });

  it('appears when a session begins after it has mounted', async () => {
    render(<TripsNavLink />);
    expect(screen.queryByRole('link', { name: 'Your trips' })).toBeNull();

    setPrincipal(ADA);

    expect(await screen.findByRole('link', { name: 'Your trips' })).toBeDefined();
  });
});
