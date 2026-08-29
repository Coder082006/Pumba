import { Suspense } from 'react';

import { ActivityCard, DestinationCard } from '@/components/catalogue/cards';
import { listActivities, listDestinations, listTags } from '@/lib/catalogue';

/**
 * Explore — SRS §24.7.
 *
 * **Each section fetches and fails on its own.** The plan is explicit that a
 * failed activity fetch must not blank the destinations, so every section is
 * its own `Suspense` boundary with its own try/catch. The alternative —
 * awaiting all three and rendering once — turns any single upstream hiccup
 * into an empty page, which is both a worse experience and a worse signal,
 * because the one broken section is no longer identifiable.
 *
 * Server-rendered. §29's NFR-P01 measures this page, and its content has to be
 * in the markup rather than fetched after hydration.
 */
export const revalidate = 30;

export const metadata = {
  title: 'Explore — destinations and activities',
  description:
    'Browse destinations, attractions and activities, and plan the whole journey before you travel.',
};

export default function ExplorePage() {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="font-display text-3xl font-bold leading-tight tracking-tight">Explore</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick a destination, then build the days around it.
        </p>
      </header>

      <Suspense fallback={<SectionSkeleton label="Interests" rows={1} />}>
        <TagChips />
      </Suspense>

      <Suspense fallback={<SectionSkeleton label="Destinations" rows={4} />}>
        <DestinationsSection />
      </Suspense>

      <Suspense fallback={<SectionSkeleton label="Activities" rows={4} />}>
        <ActivitiesSection />
      </Suspense>
    </div>
  );
}

async function TagChips() {
  let tags;
  try {
    tags = await listTags();
  } catch {
    // A missing chip row is not worth an error message — the sections below
    // are still browsable without filters, so this degrades to nothing.
    return null;
  }
  if (tags.length === 0) return null;

  return (
    <section aria-labelledby="interests">
      <h2 id="interests" className="sr-only">
        Interests
      </h2>
      <ul className="flex flex-wrap gap-2">
        {tags.map((tag) => (
          <li key={tag.slug}>
            <span className="rounded-full border border-border px-3 py-1 text-sm">
              {tag.label}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

async function DestinationsSection() {
  try {
    const { items } = await listDestinations({ limit: 12 });
    if (items.length === 0) {
      return <EmptySection title="Destinations" message="No destinations are published yet." />;
    }
    return (
      <section aria-labelledby="destinations">
        <h2 id="destinations" className="mb-4 font-display text-2xl font-bold tracking-tight">
          Destinations
        </h2>
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {items.map((destination) => (
            <DestinationCard key={destination.public_id} destination={destination} />
          ))}
        </ul>
      </section>
    );
  } catch {
    return <FailedSection title="Destinations" />;
  }
}

async function ActivitiesSection() {
  try {
    const { items } = await listActivities({ limit: 12 });
    if (items.length === 0) {
      return <EmptySection title="Activities" message="No activities are published yet." />;
    }
    return (
      <section aria-labelledby="activities">
        <h2 id="activities" className="mb-4 font-display text-2xl font-bold tracking-tight">
          Activities
        </h2>
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {items.map((activity) => (
            <ActivityCard key={activity.public_id} activity={activity} />
          ))}
        </ul>
      </section>
    );
  } catch {
    return <FailedSection title="Activities" />;
  }
}

function SectionSkeleton({ label, rows }: { label: string; rows: number }) {
  return (
    <section aria-busy="true" aria-label={`${label} loading`}>
      <div className="mb-3 h-6 w-40 rounded bg-muted" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: rows }).map((_, index) => (
          // Same box the real card occupies, so the skeleton does not shift
          // the page when it is replaced (§29, CLS).
          <div key={index} className="h-56 rounded-lg bg-muted" />
        ))}
      </div>
    </section>
  );
}

function EmptySection({ title, message }: { title: string; message: string }) {
  return (
    <section>
      <h2 className="mb-3 font-display text-2xl font-bold tracking-tight">{title}</h2>
      <p className="text-sm text-muted-foreground">{message}</p>
    </section>
  );
}

function FailedSection({ title }: { title: string }) {
  return (
    <section>
      <h2 className="mb-3 font-display text-2xl font-bold tracking-tight">{title}</h2>
      <p className="rounded-md border border-dashed border-border bg-muted p-3 text-sm text-muted-foreground">
        This section could not be loaded. Everything else on the page is still available.
      </p>
    </section>
  );
}
