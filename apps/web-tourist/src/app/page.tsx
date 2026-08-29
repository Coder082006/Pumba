import Link from 'next/link';
import { Suspense } from 'react';

import { ActivityCard, DestinationCard } from '@/components/catalogue/cards';
import { FeatureBands, FeatureBandsSkeleton } from '@/components/home/feature-bands';
import { MarketHero, MarketHeroSkeleton } from '@/components/home/market-hero';
import { listActivities, listDestinations } from '@/lib/catalogue';

/**
 * Home — SRS §24.6.
 *
 * This route was the Phase 1 diagnostic placeholder until now: a health-check
 * panel headed "Phase 1 foundation", which is what a visitor to the front door
 * of a tourism product actually saw. Commits 31–33 built Explore and the
 * detail screens and never repointed `/`, and nothing noticed, because a page
 * that renders is not a page that is right. Same family as the dead
 * navigation links — the pieces worked and the whole was never looked at.
 *
 * **What §24.6 asks for that is not here.** The active-trip card, the
 * draft-trip card with Resume, and the notification bell with its unread count
 * all read `GET /trips?status=active,draft`. `apps/api/apps/trip/` is the
 * Phase 1 skeleton — no trip, no itinerary — and notifications are §19. So the
 * trip half of this screen arrives with the trip module.
 *
 * §24.6's empty state is "an illustrated first-run prompt", which is what a
 * tourist with no trips sees — and until trips exist, *every* tourist has no
 * trips. So the honest rendering of this screen today is exactly its empty
 * state: orient, and start exploring. That is what is built.
 */
export const revalidate = 30;

export const metadata = {
  title: 'Plan your journey — Pumba',
  description:
    'Discover destinations, attractions and activities, and plan the whole journey before you travel.',
};

export default function HomePage() {
  return (
    <div className="space-y-16 sm:space-y-24">
      {/* The hero cycles the open market's own photography — Stone Town,
          Nungwi, a dhow, Jozani, a spice farm. Behind a Suspense boundary
          with a same-height fallback, so the page never changes shape while
          the gallery is being fetched.

          This is not the rotating hero I argued against. Only the first image
          is eager, so LCP measures one photograph exactly as a static hero
          does; the box is a fixed `h-hero`, so CLS is zero by construction;
          and the cycle stops entirely under `prefers-reduced-motion` rather
          than merely running faster. See `packages/ui/components/motion.tsx`. */}
      <Suspense fallback={<MarketHeroSkeleton />}>
        <MarketHero />
      </Suspense>

      {/* What the place looks like, revealed as the reader arrives at each
          one. Built from the market's destinations and their photography, so
          adding a market adds its own bands. */}
      <Suspense fallback={<FeatureBandsSkeleton />}>
        <FeatureBands />
      </Suspense>

      {/* Each carousel fetches and fails on its own, as on Explore: one broken
          section must not blank the front page. */}
      <Suspense fallback={<RowSkeleton title="Featured destinations" />}>
        <FeaturedDestinations />
      </Suspense>

      <Suspense fallback={<RowSkeleton title="Things to do" />}>
        <FeaturedActivities />
      </Suspense>
    </div>
  );
}

async function FeaturedDestinations() {
  let items;
  try {
    ({ items } = await listDestinations({ limit: 4 }));
  } catch {
    return <FailedRow title="Featured destinations" />;
  }
  if (items.length === 0) return null;

  return (
    <Row title="Featured destinations" href="/explore" linkLabel="See all destinations">
      {items.map((destination) => (
        <DestinationCard key={destination.public_id} destination={destination} />
      ))}
    </Row>
  );
}

async function FeaturedActivities() {
  let items;
  try {
    ({ items } = await listActivities({ limit: 4 }));
  } catch {
    return <FailedRow title="Things to do" />;
  }
  if (items.length === 0) return null;

  return (
    <Row title="Things to do" href="/explore" linkLabel="See all activities">
      {items.map((activity) => (
        <ActivityCard key={activity.public_id} activity={activity} />
      ))}
    </Row>
  );
}

function Row({
  title,
  href,
  linkLabel,
  children,
}: {
  title: string;
  href: string;
  linkLabel: string;
  children: React.ReactNode;
}) {
  const id = title.toLowerCase().replace(/\s+/g, '-');
  return (
    <section aria-labelledby={id}>
      <div className="mb-5 flex items-baseline justify-between gap-4">
        <h2 id={id} className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
          {title}
        </h2>
        <Link
          href={href}
          className="shrink-0 text-sm font-medium text-primary transition-colors duration-fast ease-out hover:text-primary/80"
        >
          {linkLabel}
        </Link>
      </div>
      <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">{children}</ul>
    </section>
  );
}

function RowSkeleton({ title }: { title: string }) {
  return (
    <section aria-busy="true" aria-label={`${title} loading`}>
      <div className="mb-5 h-9 w-56 rounded-md bg-muted" />
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          // The same box the real card occupies, so nothing shifts when it is
          // replaced (§29, CLS).
          <div key={index} className="h-56 rounded-lg bg-muted" />
        ))}
      </div>
    </section>
  );
}

function FailedRow({ title }: { title: string }) {
  return (
    <section>
      <h2 className="mb-3 font-display text-2xl font-bold tracking-tight sm:text-3xl">{title}</h2>
      <p className="rounded-md border border-dashed border-border bg-muted p-4 text-sm text-muted-foreground">
        This section could not be loaded. Everything else on the page is still available.
      </p>
    </section>
  );
}
