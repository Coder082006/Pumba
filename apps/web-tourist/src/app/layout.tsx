import type { Metadata } from 'next';

import { SiteFooter } from '@/components/shell/site-footer';
import { SiteHeader } from '@/components/shell/site-header';
import { Providers } from '@/lib/query-client';

import './globals.css';

export const metadata: Metadata = {
  title: 'Tourism Journey Orchestration Platform',
  description:
    'Plan, book and pay for your entire journey before you travel: airport transfer, ' +
    'accommodation, activities and transport, in one confirmed itinerary.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col font-sans">
        {/* §29's Accessibility gate: a keyboard user must be able to pass the
            navigation without tabbing through every link on every page. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:shadow"
        >
          Skip to content
        </a>
        <Providers>
          <SiteHeader />
          <main id="main" className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">
            {children}
          </main>
          <SiteFooter />
        </Providers>
      </body>
    </html>
  );
}
