import Link from 'next/link';
import { Money } from '@pumba/ui';

import type { Activity, Attraction, Destination } from '@pumba/contracts';

/**
 * Listing cards.
 *
 * Money always goes through `<Money>` — §7.2 forbids float for money anywhere,
 * and the API sends `price_per_person` as a decimal string precisely so a
 * client cannot parse it into an IEEE double on the way to the screen.
 *
 * `mediaSrc` is injected rather than built here for the same reason `Gallery`
 * takes `srcFor`: §35.7 makes the key content-hashed and the host is
 * environment configuration.
 */
export function mediaSrc(fileKey: string): string {
  const base = process.env.NEXT_PUBLIC_MEDIA_BASE_URL ?? '/media';
  return `${base}/${fileKey}`;
}

function primaryImage(media: { file_key: string; alt_text: string; is_primary: boolean }[] = []) {
  return media.find((m) => m.is_primary) ?? media[0];
}

export function DestinationCard({ destination }: { destination: Destination }) {
  const image = primaryImage(destination.media);
  return (
    <li className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <Link href={`/destinations/${destination.slug}`} className="block">
        {image ? (
          <img
            src={mediaSrc(image.file_key)}
            alt={image.alt_text || ''}
            aria-hidden={image.alt_text ? undefined : true}
            width={640}
            height={420}
            loading="lazy"
            decoding="async"
            className="aspect-[3/2] w-full object-cover"
          />
        ) : (
          <div className="aspect-[3/2] w-full bg-slate-100" />
        )}
        <div className="p-3">
          <h3 className="font-medium">{destination.name}</h3>
          {destination.summary ? (
            <p className="mt-1 line-clamp-2 text-sm text-slate-600">{destination.summary}</p>
          ) : null}
        </div>
      </Link>
    </li>
  );
}

export function ActivityCard({ activity }: { activity: Activity }) {
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-3">
      <Link href={`/activities/${activity.slug}`} className="block">
        <h3 className="font-medium">{activity.name}</h3>
        <p className="mt-1 text-sm text-slate-600">
          <span className="tabular-nums">{Math.round(activity.duration_minutes / 60)} h</span>
          {' · '}
          <Money value={{ amount: activity.price_per_person, currency: activity.currency }} />
          {' pp'}
        </p>
        {/* BR-127 (ADR 0015): the server sends null below the display
            threshold, so "New" is the only thing renderable here. There is no
            branch that could show a mean off one review, because there is no
            mean to show. */}
        <p className="mt-1 text-xs text-slate-500">
          {activity.rating_avg === null
            ? 'New'
            : `${activity.rating_avg} ★ (${activity.rating_count})`}
        </p>
      </Link>
    </li>
  );
}

export function AttractionCard({ attraction }: { attraction: Attraction }) {
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-3">
      <Link href={`/attractions/${attraction.slug}`} className="block">
        <h3 className="font-medium">{attraction.name}</h3>
        {attraction.visit_minutes ? (
          <p className="mt-1 text-sm text-slate-600 tabular-nums">
            about {attraction.visit_minutes} min
          </p>
        ) : null}
      </Link>
    </li>
  );
}
