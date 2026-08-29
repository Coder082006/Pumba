import Link from 'next/link';

/**
 * The persistent header — SRS §24.
 *
 * `<nav>` carries an accessible name because §29's NFR-P01 gate includes
 * Accessibility ≥ 95, and a page with two unnamed landmarks of the same role
 * fails that check before any content is looked at.
 *
 * **Every link points at a route that exists**, which is not a remark anybody
 * should have to make. This nav shipped with `/destinations`, `/attractions`
 * and `/activities`, and the pages built afterwards were the *detail* routes —
 * `/destinations/[slug]` and its two siblings. Three of the four links were
 * 404s from the day the shell landed, and nothing noticed: the header renders,
 * its tests pass, and `next build` lists the routes that exist without
 * checking what links to them. `__tests__/navigation.test.ts` now walks the
 * App Router directory and fails on a link that resolves to nothing.
 *
 * §24.7's Explore *is* the browse surface, so it is what the nav points at.
 * Separate per-kind index listings are not screens the SRS asks for; if they
 * are wanted, they are an addition to the plan rather than a repair.
 */
/** One nav link, with the underline that grows rather than blinks on. */
function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
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

export function SiteHeader() {
  return (
    // `sticky` rather than `fixed`: fixed takes the header out of flow and
    // every page below it needs a matching top offset, which is a constant
    // two places have to agree on for ever.
    <header className="sticky top-0 z-40 border-b border-border bg-card/85 backdrop-blur supports-[backdrop-filter]:bg-card/70">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-4 py-4 sm:px-6">
        <Link
          href="/"
          className="font-display text-xl font-bold tracking-tight text-foreground transition-colors duration-fast ease-out hover:text-primary"
        >
          Pumba
        </Link>
        <nav aria-label="Main" className="flex items-center gap-6 text-sm font-medium">
          <NavLink href="/explore">Explore</NavLink>
          <NavLink href="/stays">Where to stay</NavLink>
          <Link
            href="/login"
            className="rounded-md bg-primary px-4 py-2 text-primary-foreground shadow-sm transition-colors duration-fast ease-out hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Sign in
          </Link>
        </nav>
      </div>
    </header>
  );
}
