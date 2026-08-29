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
  // Named explicitly so `next/font` can generate a *size-adjusted* fallback
  // from these exact metrics. It is the difference between a swap that is
  // invisible and one that reflows the page: Lighthouse attributed the whole
  // of `/explore`'s 0.077 CLS to "Web font loaded", against a 0.1 budget.
  fallback: ['system-ui', 'arial'],
  adjustFontFallback: true,
});

const fraunces = Fraunces({
  subsets: ['latin'],
  display: 'optional',
  // Headings only, so the weight range is narrow on purpose: a variable font
  // shipped with its full axis is a large download for glyphs nobody sets.
  weight: ['600', '700'],
  variable: '--font-display-loaded',
  // A serif fallback, because the adjustment is computed against whatever is
  // named here. Falling back to a sans while the serif loads changes the
  // metrics twice over.
  fallback: ['Georgia', 'Times New Roman', 'serif'],
  adjustFontFallback: true,
});

/**
 * The origin every relative URL in `metadata` resolves against.
 *
 * Without it, `alternates: { canonical: '/destinations/stone-town' }` — which
 * three page files already declare — is emitted verbatim as a *relative*
 * href. `rel=canonical` must be absolute to mean anything, so what shipped
 * was a canonical tag that looked present in the markup and was invalid to
 * every crawler that read it. Lighthouse's `canonical` audit is what caught
 * it, and it is the reason the SEO gate has scored 0.92 against a threshold
 * of 1.0 since the gate was added.
 *
 * Same `NEXT_PUBLIC_SITE_URL` that `sitemap.ts` and `robots.ts` read, so the
 * three agree on the origin by construction rather than by coincidence.
 */
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000';

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
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
