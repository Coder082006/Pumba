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
 *
 * **Every hover effect is a transform or a colour, never a layout property.**
 * A card that grows its padding or its border on hover reflows the grid under
 * the pointer, which is both a CLS cost and the reason a list feels unsteady
 * to use. `translate` and `scale` are composited and move nothing around
 * them; the image scales inside a clipped box, so the card's own footprint
 * never changes.
 *
 * All of it is expressed in the duration tokens, so the `prefers-reduced-motion`
 * rule in `globals.css` flattens every one of them without a component having
 * to opt in — which is the only version of that rule that survives contact
 * with a codebase.
 */
export function mediaSrc(fileKey: string): string {
  const base = process.env.NEXT_PUBLIC_MEDIA_BASE_URL ?? '/media';
  return `${base}/${fileKey}`;
}

/**
 * The `srcset` for one media row.
 *
 * `scripts/fetch_commons_media.py` writes every photograph twice — the wide
 * file under `file_key`, and a 960px sibling as `<stem>-960.webp` — so a
 * phone is not made to download a hero. The convention lives in two places by
 * necessity, the script that writes the files and this function that names
 * them, and `media-srcset.test.ts` pins the pair together.
 *
 * Returns `undefined` for a key that does not fit the convention, so a
 * hand-added image degrades to the single `src` rather than requesting a
 * variant nobody generated.
 */
export function mediaSrcSet(fileKey: string): string | undefined {
  const stem = fileKey.replace(/\.webp$/, '');
  if (stem === fileKey) return undefined;
  return `${mediaSrc(`${stem}-960.webp`)} 960w, ${mediaSrc(fileKey)} 1600w`;
}

function primaryImage(media: { file_key: string; alt_text: string; is_primary: boolean }[] = []) {
  return media.find((m) => m.is_primary) ?? media[0];
}

/** The border, elevation and hover behaviour every card shares. */
const CARD =
  'group relative overflow-hidden rounded-xl border border-border bg-card shadow-sm ' +
  'transition-[box-shadow,transform] duration-base ease-out ' +
  'hover:-translate-y-0.5 hover:shadow-lg ' +
  'focus-within:-translate-y-0.5 focus-within:shadow-lg';

/**
 * Stretches one link over the whole card.
 *
 * The obvious alternative — wrapping the card in a single `<a>` — pulls the
 * price, the duration and the rating inside the link's accessible name, so a
 * screen reader announces each card as one long unreadable link. This keeps
 * the name to the heading and leaves the rest as ordinary text.
 */
const OVERLAY =
  'after:absolute after:inset-0 after:rounded-xl after:content-[""] outline-none ' +
  'focus-visible:after:ring-2 focus-visible:after:ring-ring';

export function DestinationCard({ destination }: { destination: Destination }) {
  const image = primaryImage(destination.media);
  return (
    <li className={CARD}>
      {/* Clipped, so the image can scale without the card resizing. The box
          keeps its aspect ratio whether or not an image exists, which is what
          stops the grid reflowing as photographs arrive. */}
      <div className="aspect-[3/2] w-full overflow-hidden bg-muted">
        {image ? (
          <img
            src={mediaSrc(image.file_key)}
            srcSet={mediaSrcSet(image.file_key)}
            // A card is at most a quarter of a 1280px grid, and full width on
            // a phone. Telling the browser that is what lets it pick the
            // 960px file instead of the hero.
            sizes="(min-width: 1024px) 25vw, (min-width: 640px) 50vw, 100vw"
            alt={image.alt_text || ''}
            aria-hidden={image.alt_text ? undefined : true}
            width={640}
            height={420}
            loading="lazy"
            decoding="async"
            className="h-full w-full object-cover transition-transform duration-slow ease-out group-hover:scale-[1.04]"
          />
        ) : null}
      </div>
      <div className="p-4">
        <h3 className="font-display text-lg font-semibold leading-snug tracking-tight">
          <Link href={`/destinations/${destination.slug}`} className={OVERLAY}>
            {destination.name}
          </Link>
        </h3>
        {destination.summary ? (
          <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
            {destination.summary}
          </p>
        ) : null}
      </div>
    </li>
  );
}

export function ActivityCard({ activity }: { activity: Activity }) {
  return (
    <li className={`${CARD} p-4`}>
      <h3 className="font-display text-lg font-semibold leading-snug tracking-tight">
        <Link href={`/activities/${activity.slug}`} className={OVERLAY}>
          {activity.name}
        </Link>
      </h3>
      <p className="mt-2 text-sm text-muted-foreground">
        <span className="tabular-nums">{Math.round(activity.duration_minutes / 60)} h</span>
        {' · '}
        <span className="font-semibold text-foreground">
          <Money value={{ amount: activity.price_per_person, currency: activity.currency }} />
        </span>
        {' pp'}
      </p>
      {/* BR-127 (ADR 0015): the server sends null below the display
          threshold, so "New" is the only thing renderable here. There is no
          branch that could show a mean off one review, because there is no
          mean to show. */}
      <p className="mt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {activity.rating_avg === null
          ? 'New'
          : `${activity.rating_avg} ★ (${activity.rating_count})`}
      </p>
    </li>
  );
}

export function AttractionCard({ attraction }: { attraction: Attraction }) {
  return (
    <li className={`${CARD} p-4`}>
      <h3 className="font-display text-lg font-semibold leading-snug tracking-tight">
        <Link href={`/attractions/${attraction.slug}`} className={OVERLAY}>
          {attraction.name}
        </Link>
      </h3>
      {attraction.visit_minutes ? (
        <p className="mt-2 text-sm tabular-nums text-muted-foreground">
          about {attraction.visit_minutes} min
        </p>
      ) : null}
    </li>
  );
}
