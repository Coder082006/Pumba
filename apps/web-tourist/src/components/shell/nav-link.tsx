import Link from 'next/link';

/**
 * One header nav link, with the underline that grows rather than blinks on.
 *
 * Lifted out of `site-header` so the signed-in links can use it too. It carries
 * no `'use client'` of its own — a `Link` and a span work in either environment,
 * and marking it would drag the server-rendered half of the header into the
 * browser bundle for nothing.
 */
export function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="group relative py-1 text-foreground/80 transition-colors duration-fast ease-out hover:text-foreground focus-visible:text-foreground focus-visible:outline-none"
    >
      {children}
      {/* A transform, never a layout property — a hover that changes width or
          padding reflows the row, which is a CLS cost paid on every pointer
          move. `scale-x` is composited. */}
      <span
        aria-hidden
        className="absolute inset-x-0 -bottom-0.5 h-px origin-left scale-x-0 bg-primary transition-transform duration-base ease-out group-hover:scale-x-100 group-focus-visible:scale-x-100"
      />
    </Link>
  );
}
