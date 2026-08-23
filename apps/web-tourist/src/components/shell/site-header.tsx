import Link from 'next/link';

/**
 * The persistent header — SRS §24.
 *
 * `<nav>` carries an accessible name because §29's NFR-P01 gate includes
 * Accessibility ≥ 95, and a page with two unnamed landmarks of the same role
 * fails that check before any content is looked at.
 */
export function SiteHeader() {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          Pumba
        </Link>
        <nav aria-label="Main" className="flex items-center gap-4 text-sm">
          <Link href="/destinations" className="hover:underline">
            Destinations
          </Link>
          <Link href="/attractions" className="hover:underline">
            Attractions
          </Link>
          <Link href="/activities" className="hover:underline">
            Activities
          </Link>
          <Link href="/login" className="hover:underline">
            Sign in
          </Link>
        </nav>
      </div>
    </header>
  );
}
