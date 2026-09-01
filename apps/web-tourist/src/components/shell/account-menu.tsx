'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { ensureSession, logout } from '@/lib/auth';
import { getPrincipal, subscribeToSession } from '@/lib/session';

/**
 * The header's account control — signed out it invites, signed in it identifies.
 *
 * **A client component inside a server-rendered header, and it has to be.**
 * ADR 0008 keeps the access token in a module variable and never persists it,
 * so nothing on the Next server can tell a signed-in visitor from an anonymous
 * one — there is no cookie a server render could read. The rest of the header
 * stays server-rendered; only this island knows about the session.
 *
 * **It subscribes rather than reads once.** The header lives in the layout, and
 * the App Router does not remount a layout on client-side navigation. Logging
 * in and being pushed to `/` would otherwise leave a "Sign in" button on screen
 * for somebody who had just signed in, until they happened to reload.
 *
 * **The session bootstrap is shared** — `ensureSession`, not `refreshSession`.
 * `/auth/refresh` rotates, and presenting a superseded token is treated as
 * theft: the server revokes the family and emails the owner. This component
 * and the guard on `/trips` both mount on the planner, so two unguarded
 * refreshes would race and sign the tourist out of their own account.
 */
export function AccountMenu() {
  const principal = useSyncExternalStore(subscribeToSession, getPrincipal, () => null);
  const [resolved, setResolved] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const router = useRouter();

  useEffect(() => {
    void ensureSession().finally(() => setResolved(true));
  }, []);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const signOut = useCallback(async () => {
    setBusy(true);
    try {
      await logout();
      setOpen(false);
      router.push('/');
    } finally {
      setBusy(false);
    }
  }, [router]);

  // Until the refresh has answered, neither state is known. A "Sign in" button
  // rendered in that gap and swapped a moment later is a flash of the wrong
  // answer on every page load for a signed-in tourist; a box of the same size
  // holds the row still (§29 measures CLS).
  if (!resolved && principal === null) {
    return <div aria-hidden className="h-9 w-9 rounded-full bg-muted" />;
  }

  if (principal === null) {
    return (
      <Link
        href="/login"
        className="rounded-md bg-primary px-4 py-2 text-primary-foreground shadow-sm transition-colors duration-fast ease-out hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        Sign in
      </Link>
    );
  }

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Your account"
        className="flex h-9 w-9 items-center justify-center rounded-full border border-border bg-muted text-foreground transition-colors duration-fast ease-out hover:bg-accent/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <UserIcon />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-50 mt-2 w-48 overflow-hidden rounded-md border border-border bg-background py-1 shadow-lg"
        >
          <MenuLink href="/trips" onNavigate={() => setOpen(false)}>
            Your trips
          </MenuLink>
          <MenuLink href="/trips/new" onNavigate={() => setOpen(false)}>
            Plan a trip
          </MenuLink>
          <div className="my-1 border-t border-border" />
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={() => void signOut()}
            className="block w-full px-3 py-2 text-left text-sm text-foreground transition-colors duration-fast ease-out hover:bg-muted disabled:opacity-60"
          >
            {busy ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function MenuLink({
  href,
  onNavigate,
  children,
}: {
  href: string;
  onNavigate: () => void;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      role="menuitem"
      onClick={onNavigate}
      className="block px-3 py-2 text-sm text-foreground transition-colors duration-fast ease-out hover:bg-muted"
    >
      {children}
    </Link>
  );
}

/** `aria-hidden`: the button already carries the accessible name. */
function UserIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-5 w-5"
    >
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}
