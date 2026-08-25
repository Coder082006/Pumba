import Link from 'next/link';
import { Suspense } from 'react';

import { ActivityCard, DestinationCard } from '@/components/catalogue/cards';
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
    <div className="space-y-12">
      <section className="rounded-xl border border-slate-200 bg-gradient-to-br from-slate-50 to-white p-8 sm:p-12">
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight sm:text-5xl">
          Plan the whole journey before you travel.
        </h1>
        <p className="mt-4 max-w-xl text-slate-600">
          Browse destinations, pick the things worth doing, and build the days around where you
          are staying — transfers included.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          {/* §24.6's "prominent Plan a Trip button". It points at Explore
              rather than at a planner: the Trip Planner is §24.14 and belongs
              to the trip module, and a button leading to a 404 is the defect
              `navigation.test.ts` now fails the build for. */}
          <Link
            href="/explore"
            className="rounded-md bg-slate-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-slate-700"
          >
            Start exploring
          </Link>
          <Link
            href="/stays"
            className="rounded-md border border-slate-300 px-5 py-2.5 text-sm font-medium hover:bg-slate-50"
          >
            Where are you staying?
          </Link>
        </div>
      </section>

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
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 id={id} className="text-lg font-semibold">
          {title}
        </h2>
        <Link href={href} className="text-sm text-slate-600 hover:underline">
          {linkLabel}
        </Link>
      </div>
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">{children}</ul>
    </section>
  );
}

function RowSkeleton({ title }: { title: string }) {
  return (
    <section aria-busy="true" aria-label={`${title} loading`}>
      <div className="mb-3 h-6 w-48 rounded bg-slate-100" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          // The same box the real card occupies, so nothing shifts when it is
          // replaced (§29, CLS).
          <div key={index} className="h-56 rounded-lg bg-slate-100" />
        ))}
      </div>
    </section>
  );
}

function FailedRow({ title }: { title: string }) {
  return (
    <section>
      <h2 className="mb-2 text-lg font-semibold">{title}</h2>
      <p className="rounded-md border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-600">
        This section could not be loaded. Everything else on the page is still available.
      </p>
    </section>
  );
}
