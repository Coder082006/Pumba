import Link from 'next/link';
import type { Metadata } from 'next';

import { MapPanel } from '@/components/catalogue/map-panel';
import { StayPicker } from '@/components/catalogue/stay-picker';
import { stayLimits } from '@/lib/config';
import { getDestination, listAccommodation, listDestinations } from '@/lib/catalogue';
import type { Accommodation } from '@pumba/contracts';

/**
 * Where are you staying — SRS §24.11 as amended (ADR 0013).
 *
 * **Route.** The plan puts this at `/trip/[id]/stay`, and that is where it
 * belongs: it is a step inside a trip. There is no trip — `apps/api/apps/trip`
 * is still a Phase 1 skeleton — so a `[id]` segment here would be a parameter
 * with nothing behind it, and inventing an id to satisfy the URL is the same
 * species of fabrication as inventing a coordinate. Scoped by destination
 * instead until the trip module lands, when the screen moves and this path
 * redirects.
 *
 * **Not indexed.** `robots: index: false`, and permanently rather than
 * pending: this is a planning tool operating on a tourist's own trip, not the
 * §24.8 SEO surface. The destination, attraction and activity pages are what
 * a crawler is meant to find, and they link here.
 */
export const revalidate = 30;

export const metadata: Metadata = {
  title: 'Where are you staying?',
  description: 'Tell us where you are staying so we can plan the days around it.',
  robots: { index: false, follow: true },
};

interface PageProps {
  searchParams: Promise<{ destination?: string }>;
}

export default async function StaysPage({ searchParams }: PageProps) {
  const { destination } = await searchParams;

  if (!destination) return <ChooseDestination />;

  // Independent: a `/config` outage must cost the dates, not the properties,
  // and an empty accommodation list must not hide the rest of the screen.
  // `allSettled` is what keeps one failure from becoming three.
  const [propertiesResult, destinationResult, limitsResult] = await Promise.allSettled([
    listAccommodation({ destination, limit: 50 }),
    getDestination(destination),
    stayLimits(),
  ]);

  if (destinationResult.status === 'rejected') {
    return (
      <Shell>
        <p className="rounded-md border border-dashed border-border bg-muted p-4 text-sm text-muted-foreground">
          We could not load that destination.{' '}
          <Link href="/stays" className="underline">
            Pick another
          </Link>
          .
        </p>
      </Shell>
    );
  }

  const properties: Accommodation[] =
    propertiesResult.status === 'fulfilled' ? propertiesResult.value.items : [];
  const maxNights = limitsResult.status === 'fulfilled' ? limitsResult.value.maxNights : null;
  const destinationName = destinationResult.value.name;

  return (
    <Shell>
      {propertiesResult.status === 'rejected' ? (
        // Distinguished from "no properties listed", which the picker says on
        // its own. An outage and an empty catalogue look identical to a
        // tourist otherwise, and only one of them is worth retrying.
        <p
          role="status"
          className="rounded-md border border-dashed border-border bg-muted p-3 text-sm text-muted-foreground"
        >
          The property list could not be loaded just now. You can still type where you are staying.
        </p>
      ) : null}

      <StayPicker
        properties={properties}
        destinationName={destinationName}
        maxNights={maxNights}
        map={
          <MapPanel
            title={`Properties in ${destinationName}`}
            aspectRatio="4 / 3"
            pins={properties.map((property) => ({
              id: property.public_id,
              latitude: Number(property.latitude),
              longitude: Number(property.longitude),
              label: property.name,
            }))}
            center={{
              latitude: Number(destinationResult.value.latitude),
              longitude: Number(destinationResult.value.longitude),
            }}
          />
        }
      />
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-3xl font-bold leading-tight tracking-tight">Where are you staying?</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your stay anchors the trip — transfers and day trips are planned around it. No prices, no
          availability: just where and when.
        </p>
      </header>
      {children}
    </div>
  );
}

async function ChooseDestination() {
  let destinations;
  try {
    ({ items: destinations } = await listDestinations({ limit: 24 }));
  } catch {
    return (
      <Shell>
        <p className="rounded-md border border-dashed border-border bg-muted p-4 text-sm text-muted-foreground">
          Destinations could not be loaded. Please try again shortly.
        </p>
      </Shell>
    );
  }

  return (
    <Shell>
      <h2 className="text-sm font-medium">Which destination?</h2>
      {destinations.length === 0 ? (
        <p className="text-sm text-muted-foreground">No destinations are published yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {destinations.map((item) => (
            <li key={item.public_id}>
              <Link
                href={`/stays?destination=${encodeURIComponent(item.slug)}`}
                className="inline-block rounded-full border border-border px-3 py-1 text-sm hover:bg-muted"
              >
                {item.name}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Shell>
  );
}
