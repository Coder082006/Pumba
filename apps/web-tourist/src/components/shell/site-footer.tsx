import Link from 'next/link';

/**
 * The persistent footer.
 *
 * **Nothing here claims the site works offline.** ADR 0002 makes the web
 * client the MVP tourist surface and is explicit that it makes no offline
 * guarantee: no "available offline" copy, no install prompt implying one, and
 * no service worker caching that suggests an itinerary survives a lost
 * connection. §41.10's answer to offline is the emailed PDF and the calendar
 * export, which belong to the trip-confirmation path — so the honest thing for
 * this footer to do is say nothing about it at all.
 */
export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-slate-200 bg-slate-50">
      <div className="mx-auto flex max-w-5xl flex-col gap-2 px-4 py-6 text-sm text-slate-600 sm:flex-row sm:items-center sm:justify-between">
        <p>Plan your whole journey before you travel.</p>
        <nav aria-label="Footer" className="flex gap-4">
          <Link href="/help" className="hover:underline">
            Help centre
          </Link>
          <Link href="/terms" className="hover:underline">
            Terms
          </Link>
          <Link href="/privacy" className="hover:underline">
            Privacy
          </Link>
        </nav>
      </div>
    </footer>
  );
}
