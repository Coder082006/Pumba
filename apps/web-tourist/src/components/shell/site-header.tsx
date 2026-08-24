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
export function SiteHeader() {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          Pumba
        </Link>
        <nav aria-label="Main" className="flex items-center gap-4 text-sm">
          <Link href="/explore" className="hover:underline">
            Explore
          </Link>
          <Link href="/stays" className="hover:underline">
            Where to stay
          </Link>
          <Link href="/login" className="hover:underline">
            Sign in
          </Link>
        </nav>
      </div>
    </header>
  );
}
