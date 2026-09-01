/**
 * The header's account control — SRS §24, ADR 0008.
 *
 * Three behaviours, and none of them is cosmetic:
 *
 *   1. A signed-in tourist must not be shown "Sign in". The header is
 *      server-rendered and the token lives in the browser's memory, so the
 *      server cannot know — this island is the only thing that can.
 *   2. It has to notice a session that begins *after* it mounted. The header
 *      is in the layout, which the App Router does not remount on a
 *      client-side navigation, so logging in and being pushed to `/` would
 *      otherwise leave the wrong button on screen indefinitely.
 *   3. Signing out has to clear the session even if the request fails —
 *      otherwise the browser keeps a live token and the user cannot tell.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountMenu } from '@/components/shell/account-menu';
import { clearSession, setPrincipal } from '@/lib/session';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));

const ensureSession = vi.fn();
const logout = vi.fn();
vi.mock('@/lib/auth', () => ({
  ensureSession: () => ensureSession(),
  logout: () => logout(),
}));

const ADA = { publicId: 'a-uuid', roles: ['TOURIST'] };

beforeEach(() => {
  push.mockReset();
  ensureSession.mockReset().mockResolvedValue(false);
  logout.mockReset().mockImplementation(async () => {
    clearSession();
  });
  clearSession();
});

afterEach(() => {
  clearSession();
});

describe('when nobody is signed in', () => {
  it('offers the sign-in button', async () => {
    render(<AccountMenu />);
    expect(await screen.findByRole('link', { name: 'Sign in' })).toBeDefined();
  });

  it('shows no account button', async () => {
    render(<AccountMenu />);
    await screen.findByRole('link', { name: 'Sign in' });
    expect(screen.queryByRole('button', { name: 'Your account' })).toBeNull();
  });
});

describe('when a session is restored on load', () => {
  it('shows the account button instead of Sign in', async () => {
    ensureSession.mockImplementation(async () => {
      setPrincipal(ADA);
      return true;
    });

    render(<AccountMenu />);
    expect(await screen.findByRole('button', { name: 'Your account' })).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Sign in' })).toBeNull();
  });

  it('asks for the session exactly once', async () => {
    /**
     * `/auth/refresh` rotates, and a superseded token is treated as theft —
     * the server revokes the family and signs the tourist out everywhere. This
     * component must go through the shared bootstrap rather than refreshing on
     * its own.
     */
    render(<AccountMenu />);
    await screen.findByRole('link', { name: 'Sign in' });
    expect(ensureSession).toHaveBeenCalledTimes(1);
  });
});

describe('when the session begins after mounting', () => {
  it('swaps to the account button without a reload', async () => {
    /**
     * The bug this exists to prevent. The header sits in the layout, which the
     * App Router does not remount on `router.push('/')` after a login — so a
     * component that read the session once would keep showing "Sign in" to
     * somebody who had just signed in.
     */
    render(<AccountMenu />);
    await screen.findByRole('link', { name: 'Sign in' });

    setPrincipal(ADA);
    expect(await screen.findByRole('button', { name: 'Your account' })).toBeDefined();
  });

  it('swaps back when the session ends', async () => {
    setPrincipal(ADA);
    render(<AccountMenu />);
    await screen.findByRole('button', { name: 'Your account' });

    clearSession();
    expect(await screen.findByRole('link', { name: 'Sign in' })).toBeDefined();
  });
});

describe('the menu', () => {
  async function openMenu() {
    setPrincipal(ADA);
    render(<AccountMenu />);
    fireEvent.click(await screen.findByRole('button', { name: 'Your account' }));
  }

  it('is closed until the button is pressed', async () => {
    setPrincipal(ADA);
    render(<AccountMenu />);
    await screen.findByRole('button', { name: 'Your account' });
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('leads to the trips the tourist owns', async () => {
    await openMenu();
    expect(screen.getByRole('menuitem', { name: 'Your trips' })).toBeDefined();
    expect(screen.getByRole('menuitem', { name: 'Plan a trip' })).toBeDefined();
  });

  it('closes on Escape', async () => {
    await openMenu();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
  });

  it('reports its state to a screen reader', async () => {
    await openMenu();
    expect(
      screen.getByRole('button', { name: 'Your account' }).getAttribute('aria-expanded'),
    ).toBe('true');
  });
});

describe('signing out', () => {
  it('ends the session and returns to the landing page', async () => {
    setPrincipal(ADA);
    render(<AccountMenu />);
    fireEvent.click(await screen.findByRole('button', { name: 'Your account' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Sign out/ }));

    await waitFor(() => expect(logout).toHaveBeenCalled());
    await waitFor(() => expect(push).toHaveBeenCalledWith('/'));
  });

  it('shows the sign-in button again afterwards', async () => {
    setPrincipal(ADA);
    render(<AccountMenu />);
    fireEvent.click(await screen.findByRole('button', { name: 'Your account' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Sign out/ }));

    expect(await screen.findByRole('link', { name: 'Sign in' })).toBeDefined();
  });
});
