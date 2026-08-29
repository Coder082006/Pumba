import type { Metadata } from 'next';
import { Fraunces, Inter } from 'next/font/google';

import { SiteFooter } from '@/components/shell/site-footer';
import { SiteHeader } from '@/components/shell/site-header';
import { Providers } from '@/lib/query-client';

import './globals.css';

/*
 * The typefaces the preset has been asking for since Phase 1.
 *
 * `--font-sans` and `--font-display` are referenced by
 * `@pumba/ui/tailwind-preset` and were defined nowhere, so `font-sans`
 * resolved to `system-ui` and the site had no typeface of its own — a page
 * set in whatever the visitor's OS happened to ship.
 *
 * `next/font` **self-hosts** these. Nothing is fetched from Google at
 * runtime, which matters twice: no third-party request on the critical path
 * for the §29 LCP budget, and no font host in the privacy story that a PDPC
 * filing would have to describe.
 *
 * `display: 'swap'` so text paints in the fallback immediately rather than
 * holding the first paint hostage to a font file. The fallback stacks in
 * `globals.css` are metric-near, so the swap is not a lurch.
 */
const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-sans-loaded',
});

const fraunces = Fraunces({
  subsets: ['latin'],
  display: 'swap',
  // Headings only, so the weight range is narrow on purpose: a variable font
  // shipped with its full axis is a large download for glyphs nobody sets.
  weight: ['600', '700'],
  variable: '--font-display-loaded',
});

export const metadata: Metadata = {
  title: 'Tourism Journey Orchestration Platform',
  description:
    'Plan, book and pay for your entire journey before you travel: airport transfer, ' +
    'accommodation, activities and transport, in one confirmed itinerary.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${fraunces.variable}`}>
      <body className="flex min-h-screen flex-col font-sans">
        {/* §29's Accessibility gate: a keyboard user must be able to pass the
            navigation without tabbing through every link on every page. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:text-card-foreground focus:shadow-md focus:outline-none focus:ring-2 focus:ring-ring"
        >
          Skip to content
        </a>
        <Providers>
          <SiteHeader />
          <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
            {children}
          </main>
          <SiteFooter />
        </Providers>
      </body>
    </html>
  );
}
