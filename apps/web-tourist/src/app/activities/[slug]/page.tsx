import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { Gallery, Money } from '@pumba/ui';

import { mediaSrc } from '@/components/catalogue/cards';
import { MapPanel } from '@/components/catalogue/map-panel';
import { ApiRequestError, apiFetch } from '@/lib/api';
import { fallbackDescription } from '@/lib/metadata';
import type { Activity } from '@pumba/contracts';

/**
 * Activity — SRS §24.10.
 *
 * Two things this page deliberately does **not** show, both because the data
 * does not exist rather than because they were forgotten.
 *
 * **No departures calendar.** §24.10's design has a month grid with per-date
 * seat counts. Those come from `activity_departure`, which ADR 0011 moved to
 * `inventory` and whose materialisation is Phase 5 — there is no endpoint
 * serving them, and there will not be one this phase. Rendering an empty
 * calendar would look like an activity with no availability, which is a
 * commercial lie about a provider. The panel states its own absence instead.
 *
 * **No converted price.** The design shows "TZS 117,000 indicative" beside the
 * USD figure. The `Activity` schema's own docstring rules it out: *"§18.4 puts
 * conversion at quote time, and a display conversion is an `IndicativeAmount`
 * applied over this — which is a different thing with a different label and a
 * different half-life."* The API sends one price in one currency, and that is
 * what appears.
 */
export const revalidate = 30;
export const dynamicParams = true;

interface Params {
  params: Promise<{ slug: string }>;
}

const cached = { next: { revalidate: 30 } } as Parameters<typeof apiFetch>[1];

async function load(slug: string): Promise<Activity> {
  try {
    return await apiFetch<Activity>(`/activities/${encodeURIComponent(slug)}`, cached);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) notFound();
    throw error;
  }
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params;
  try {
    const activity = await load(slug);
    return {
      title: `${activity.name} — ${activity.destination.name}`,
      description: activity.summary ?? fallbackDescription('activity', activity.name),
      alternates: { canonical: `/activities/${activity.slug}` },
    };
  } catch {
    // §24.8: never a page with no description. See `lib/metadata`.
    return { title: 'Activity', description: fallbackDescription('activity') };
  }
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

export default async function ActivityPage({ params }: Params) {
  const { slug } = await params;
  const activity = await load(slug);
  const latitude = Number(activity.latitude);
  const longitude = Number(activity.longitude);
  const inclusions = asList(activity.inclusions);
  const exclusions = asList(activity.exclusions);
  const requirements = asList(activity.requirements);

  return (
    <article className="space-y-8">
      <Gallery images={activity.media} srcFor={mediaSrc} priority />

      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="font-display text-3xl font-bold leading-tight tracking-tight sm:text-4xl">{activity.name}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{activity.destination.name}</p>
        </div>
        {/* BR-127 (ADR 0015). The server sends null below the display
            threshold, so there is no mean here to render off one review —
            "New on the platform" is the only available branch. */}
        <p className="text-sm text-muted-foreground">
          {activity.rating_avg === null
            ? '★ New on the platform'
            : `★ ${activity.rating_avg} (${activity.rating_count})`}
        </p>
      </header>

      <div className="grid gap-8 sm:grid-cols-2">
        <section aria-labelledby="details" className="space-y-4">
          <h2 id="details" className="sr-only">
            Details
          </h2>
          <p className="text-sm">
            <span className="tabular-nums">{Math.round(activity.duration_minutes / 60)} hours</span>
            {' · '}
            <span className="tabular-nums">
              {activity.min_pax}–{activity.max_pax} people
            </span>
          </p>
          {activity.meeting_point ? (
            <p className="text-sm">
              <span className="font-medium">Meeting point:</span> {activity.meeting_point}
            </p>
          ) : null}
          <p className="text-sm text-muted-foreground">
            {activity.confirmation_mode === 'INSTANT'
              ? 'Confirms instantly'
              : 'Confirmed by the provider on request'}
          </p>

          {activity.description ? (
            <p className="leading-relaxed">{activity.description}</p>
          ) : null}

          {inclusions.length > 0 ? <Bullets title="Includes" items={inclusions} mark="✓" /> : null}
          {exclusions.length > 0 ? <Bullets title="Excludes" items={exclusions} mark="✕" /> : null}
          {requirements.length > 0 ? (
            <Bullets title="Requirements" items={requirements} mark="•" />
          ) : null}
          {activity.min_age !== null ? (
            <p className="text-sm">Minimum age {activity.min_age}</p>
          ) : null}

          <MapPanel
            title={`Meeting point for ${activity.name}`}
            aspectRatio="4 / 3"
            pins={[{ id: activity.public_id, latitude, longitude, label: activity.name }]}
          />
        </section>

        <section aria-labelledby="booking" className="space-y-4">
          <h2 id="booking" className="font-display text-xl font-bold tracking-tight">
            From{' '}
            <Money
              value={{ amount: activity.price_per_person, currency: activity.currency }}
              className="font-semibold"
            />{' '}
            <span className="text-sm font-normal text-muted-foreground">per person</span>
          </h2>

          <DeparturesUnavailable />

          <button
            type="button"
            disabled
            className="w-full cursor-not-allowed rounded-md border border-border bg-muted px-4 py-2 text-sm text-muted-foreground"
          >
            Add to trip — coming soon
          </button>
        </section>
      </div>

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context': 'https://schema.org',
            '@type': 'Product',
            name: activity.name,
            description: activity.summary ?? activity.description,
            offers: {
              '@type': 'Offer',
              price: activity.price_per_person,
              priceCurrency: activity.currency,
              // Deliberately omitted: `availability`. §24.10's design carries
              // it, but availability lives in `activity_departure`, which is
              // Phase 5 — asserting InStock or OutOfStock here would publish a
              // guess about a provider's calendar to search engines.
            },
          }),
        }}
      />
    </article>
  );
}

function Bullets({ title, items, mark }: { title: string; items: string[]; mark: string }) {
  return (
    <div>
      <h3 className="text-sm font-medium">{title}</h3>
      <ul className="mt-1 space-y-1 text-sm text-foreground">
        {items.map((item) => (
          <li key={item}>
            <span aria-hidden="true">{mark}</span> {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DeparturesUnavailable() {
  return (
    <div className="rounded-md border border-dashed border-border bg-muted p-3 text-sm text-muted-foreground">
      <p className="font-medium text-foreground">Dates and availability</p>
      <p className="mt-1">
        Live departure dates are not available yet. Availability is confirmed when you add this
        activity to a trip.
      </p>
    </div>
  );
}
