import type { Metadata } from 'next';

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
      <body className="min-h-screen font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
