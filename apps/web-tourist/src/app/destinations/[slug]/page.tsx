import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { Suspense } from 'react';

import { ActivityCard, AttractionCard, mediaSrc } from '@/components/catalogue/cards';
import { MapPanel } from '@/components/catalogue/map-panel';
import { ApiRequestError } from '@/lib/api';
import { getDestination, listActivities, listAttractions } from '@/lib/catalogue';

/**
 * Destination — SRS §24.8, and the page §29's NFR-P01 gate is measured on.
 *
 * Server-rendered with `revalidate`, and `dynamicParams` left on: §4.1 requires
 * a destination published in the console to appear without a deployment, so a
 * slug that did not exist at build time has to render.
 *
 * The hero is the LCP element — `priority`, explicit dimensions, no
 * client-side data above the fold. Tabs below load independently, so one
 * failing section does not blank the screen (§24.8).
 */
export const revalidate = 30;
export const dynamicParams = true;

interface Params {
  params: Promise<{ slug: string }>;
}

async function load(slug: string) {
  try {
    return await getDestination(slug);
  } catch (error) {
    // A withdrawn or unpublished destination is a 404 from the API (§30.3
    // returns 404 rather than 403 so that absence and inaccessibility are
    // indistinguishable). Anything else is a real fault and must not be
    // disguised as "not found".
    if (error instanceof ApiRequestError && error.status === 404) notFound();
    throw error;
  }
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params;
  let destination;
  try {
    destination = await getDestination(slug);
  } catch {
    return { title: 'Destination' };
  }

  const image = destination.media?.find((m) => m.is_primary) ?? destination.media?.[0];
  return {
    title: `${destination.name} — attractions, activities and places to stay`,
    description: destination.summary ?? undefined,
    alternates: { canonical: `/destinations/${destination.slug}` },
    openGraph: {
      title: destination.name,
      description: destination.summary ?? undefined,
      images: image ? [{ url: mediaSrc(image.file_key), alt: image.alt_text }] : undefined,
    },
  };
}

export default async function DestinationPage({ params }: Params) {
  const { slug } = await params;
  const destination = await load(slug);
  const hero = destination.media?.find((m) => m.is_primary) ?? destination.media?.[0];

  const latitude = Number(destination.latitude);
  const longitude = Number(destination.longitude);

  return (
    <article className="space-y-8">
      {hero ? (
        <img
          src={mediaSrc(hero.file_key)}
          alt={hero.alt_text || ''}
          aria-hidden={hero.alt_text ? undefined : true}
          width={hero.width}
          height={hero.height}
          // The LCP element. Eager and high priority; everything else lazy.
          loading="eager"
          fetchPriority="high"
          decoding="sync"
          className="aspect-[16/7] w-full rounded-lg object-cover"
        />
      ) : null}

      <header>
        <h1 className="text-3xl font-semibold tracking-tight">{destination.name}</h1>
        <p className="mt-1 text-sm text-slate-600">
          {destination.region.name} · {destination.region.country.name} · {destination.timezone} ·
          prices in {destination.default_currency}
        </p>
      </header>

      {destination.description ? (
        <p className="max-w-prose leading-relaxed">{destination.description}</p>
      ) : null}

      <section aria-labelledby="getting-here" className="grid gap-4 sm:grid-cols-2">
        <h2 id="getting-here" className="sr-only">
          Where it is
        </h2>
        <MapPanel
          title={`Map of ${destination.name}`}
          aspectRatio="4 / 3"
          zoom={11}
          center={{ latitude, longitude }}
          pins={[
            { id: destination.public_id, latitude, longitude, label: destination.name },
          ]}
        />
        <dl className="self-center text-sm">
          <dt className="font-medium">Coordinates</dt>
          <dd className="tabular-nums text-slate-600">
            {destination.latitude}, {destination.longitude}
          </dd>
          <dt className="mt-3 font-medium">Local time zone</dt>
          <dd className="text-slate-600">{destination.timezone}</dd>
        </dl>
      </section>

      <Suspense fallback={<TabSkeleton label="Attractions" />}>
        <AttractionsTab slug={destination.slug} />
      </Suspense>

      <Suspense fallback={<TabSkeleton label="Activities" />}>
        <ActivitiesTab slug={destination.slug} />
      </Suspense>

      {/* JSON-LD. Only measured facts: `geo` and the containment chain come
          straight from the row. No distance appears here — §12.6 makes a
          haversine estimate APPROXIMATE, and structured data has nowhere to
          put the "approximate" label, so publishing one would be a
          fabrication sent to search engines. */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context': 'https://schema.org',
            '@type': 'TouristDestination',
            name: destination.name,
            description: destination.summary ?? destination.description ?? undefined,
            geo: {
              '@type': 'GeoCoordinates',
              latitude: destination.latitude,
              longitude: destination.longitude,
            },
            containedInPlace: {
              '@type': 'AdministrativeArea',
              name: destination.region.name,
              containedInPlace: {
                '@type': 'Country',
                name: destination.region.country.name,
              },
            },
          }),
        }}
      />
    </article>
  );
}

async function AttractionsTab({ slug }: { slug: string }) {
  try {
    const { items } = await listAttractions({ destination: slug, limit: 12 });
    if (items.length === 0) return null;
    return (
      <section aria-labelledby="attractions">
        <h2 id="attractions" className="mb-3 text-lg font-semibold">
          Attractions
        </h2>
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((attraction) => (
            <AttractionCard key={attraction.public_id} attraction={attraction} />
          ))}
        </ul>
      </section>
    );
  } catch {
    return <FailedTab title="Attractions" />;
  }
}

async function ActivitiesTab({ slug }: { slug: string }) {
  try {
    const { items } = await listActivities({ destination: slug, limit: 12 });
    if (items.length === 0) return null;
    return (
      <section aria-labelledby="activities">
        <h2 id="activities" className="mb-3 text-lg font-semibold">
          Activities
        </h2>
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((activity) => (
            <ActivityCard key={activity.public_id} activity={activity} />
          ))}
        </ul>
      </section>
    );
  } catch {
    return <FailedTab title="Activities" />;
  }
}

function TabSkeleton({ label }: { label: string }) {
  return (
    <section aria-busy="true" aria-label={`${label} loading`}>
      <div className="mb-3 h-6 w-36 rounded bg-slate-100" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <div key={index} className="h-24 rounded-lg bg-slate-100" />
        ))}
      </div>
    </section>
  );
}

function FailedTab({ title }: { title: string }) {
  return (
    <section>
      <h2 className="mb-2 text-lg font-semibold">{title}</h2>
      <p className="rounded-md border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-600">
        This section could not be loaded. The rest of the page is unaffected.
      </p>
    </section>
  );
}
