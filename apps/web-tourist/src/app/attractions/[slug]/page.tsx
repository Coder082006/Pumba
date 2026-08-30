import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { Gallery } from '@pumba/ui';

import { mediaSrc } from '@/components/catalogue/cards';
import { MapPanel } from '@/components/catalogue/map-panel';
import { ApiRequestError, apiFetch } from '@/lib/api';
import { weekTable, type WeekRow } from '@/lib/opening-hours';
import { fallbackDescription } from '@/lib/metadata';
import type { Attraction } from '@pumba/contracts';

/**
 * Attraction — SRS §24.9.
 *
 * The week table is the visible proof of the §15.2 timezone rule: "today" is
 * computed in the *destination's* zone and the table says which zone it is
 * showing. See `lib/opening-hours.ts` for why that is not the viewer's clock.
 */
export const revalidate = 30;
export const dynamicParams = true;

interface Params {
  params: Promise<{ slug: string }>;
}

const cached = { next: { revalidate: 30 } } as Parameters<typeof apiFetch>[1];

async function load(slug: string): Promise<Attraction> {
  try {
    return await apiFetch<Attraction>(`/attractions/${encodeURIComponent(slug)}`, cached);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) notFound();
    throw error;
  }
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params;
  try {
    const attraction = await load(slug);
    return {
      title: `${attraction.name} — ${attraction.destination.name}`,
      description:
        attraction.summary ?? fallbackDescription('attraction', attraction.name),
      alternates: { canonical: `/attractions/${attraction.slug}` },
    };
  } catch {
    // §24.8: never a page with no description. See `lib/metadata`.
    return { title: 'Attraction', description: fallbackDescription('attraction') };
  }
}

export default async function AttractionPage({ params }: Params) {
  const { slug } = await params;
  const attraction = await load(slug);
  const timeZone = attraction.destination.timezone;
  const rows = weekTable(attraction.opening_hours, { timeZone });

  const latitude = Number(attraction.latitude);
  const longitude = Number(attraction.longitude);

  return (
    <article className="space-y-8">
      <Gallery images={attraction.media} srcFor={mediaSrc} priority />

      <header>
        <h1 className="font-display text-3xl font-bold leading-tight tracking-tight sm:text-4xl">{attraction.name}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {attraction.destination.name} › {attraction.destination.region.name}
        </p>
      </header>

      <div className="grid gap-8 sm:grid-cols-2">
        <section aria-labelledby="about" className="space-y-4">
          <h2 id="about" className="font-display text-xl font-bold tracking-tight">
            About
          </h2>
          <p className="leading-relaxed">{attraction.description}</p>
          {attraction.visit_minutes ? (
            <p className="text-sm">
              <span className="font-medium">Recommended visit</span>{' '}
              <span className="tabular-nums">{attraction.visit_minutes} min</span>
            </p>
          ) : null}
          {attraction.tags.length > 0 ? (
            <p className="text-sm text-muted-foreground">Tags {attraction.tags.join(' · ')}</p>
          ) : null}
          {attraction.accessibility_notes ? (
            <p className="text-sm text-muted-foreground">{attraction.accessibility_notes}</p>
          ) : null}

          <MapPanel
            title={`Map of ${attraction.name}`}
            aspectRatio="4 / 3"
            pins={[{ id: attraction.public_id, latitude, longitude, label: attraction.name }]}
          />
        </section>

        <section aria-labelledby="hours">
          <h2 id="hours" className="font-display text-xl font-bold tracking-tight">
            Opening hours
          </h2>
          {rows ? <OpeningHoursTable rows={rows} timeZone={timeZone} /> : <NoPublishedHours />}
        </section>
      </div>

      {attraction.entrance_fee && attraction.fee_currency ? (
        // §15.3's wording, not a paraphrase. The distinction it draws is the
        // whole point: this money is not in the trip total, and a tourist who
        // assumes otherwise arrives without cash.
        <p
          role="note"
          className="rounded-md border border-warning-border bg-warning p-3 text-sm text-warning-foreground"
        >
          Entrance fee {attraction.fee_currency} {attraction.entrance_fee} per person — paid on
          site, not included in your trip total.
        </p>
      ) : null}

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context': 'https://schema.org',
            '@type': 'TouristAttraction',
            name: attraction.name,
            description: attraction.summary ?? attraction.description,
            geo: {
              '@type': 'GeoCoordinates',
              latitude: attraction.latitude,
              longitude: attraction.longitude,
            },
            // Derived from the same parsed structure the table renders — one
            // source, two outputs, so the page and the crawler cannot disagree.
            openingHoursSpecification: (rows ?? []).flatMap((row) =>
              row.ranges.map(([opens, closes]) => ({
                '@type': 'OpeningHoursSpecification',
                dayOfWeek: DAY_URLS[row.key],
                opens,
                closes,
              })),
            ),
          }),
        }}
      />
    </article>
  );
}

const DAY_URLS: Record<string, string> = {
  mon: 'https://schema.org/Monday',
  tue: 'https://schema.org/Tuesday',
  wed: 'https://schema.org/Wednesday',
  thu: 'https://schema.org/Thursday',
  fri: 'https://schema.org/Friday',
  sat: 'https://schema.org/Saturday',
  sun: 'https://schema.org/Sunday',
};

function OpeningHoursTable({ rows, timeZone }: { rows: WeekRow[]; timeZone: string }) {
  return (
    <>
      <table className="mt-2 w-full text-sm">
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className={row.isToday ? 'font-medium' : undefined}>
              <th scope="row" className="py-1 pr-4 text-left font-normal">
                {row.isToday ? 'Today' : row.label}
              </th>
              <td className="py-1 tabular-nums">
                {row.ranges.length === 0
                  ? 'Closed'
                  : row.ranges.map(([open, close]) => `${open} – ${close}`).join(', ')}
                {row.exceptionReason ? (
                  <span className="text-muted-foreground"> — {row.exceptionReason}</span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {/* Which clock these times belong to. Without it the table is ambiguous
          for every reader who is not standing in the destination. */}
      <p className="mt-2 text-xs text-muted-foreground">Times shown in {timeZone}.</p>
    </>
  );
}

function NoPublishedHours() {
  return (
    <p className="mt-2 text-sm text-muted-foreground">
      Opening hours have not been published for this attraction.
    </p>
  );
}
