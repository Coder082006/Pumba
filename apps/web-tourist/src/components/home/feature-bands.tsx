import Link from 'next/link';
import { ImageCredit, Reveal, type GalleryImage } from '@pumba/ui';

import { mediaSrc, mediaSrcSet } from '@/components/catalogue/cards';
import { listDestinations } from '@/lib/catalogue';

/**
 * What the place actually looks like — SRS §24.6, §24.8.
 *
 * Alternating bands, each revealing as the reader reaches it. The content is
 * the market's destinations and their own photography, so this is Zanzibar
 * today and Arusha the day somebody creates the rows: §4.2 again, and the
 * reason the market tier exists.
 *
 * **Only destinations that have a photograph appear.** A band is
 * image-and-copy side by side, and half of one is not a design — it is a
 * broken-looking row. Four of Zanzibar's ten destinations currently have no
 * usable public-domain or CC BY image on Commons, so they are absent here and
 * present on Explore, where a card renders correctly without one.
 *
 * The reveal animates transform and opacity only, and the whole run is
 * disarmed by `prefers-reduced-motion` from `globals.css` — see
 * `packages/ui/src/components/motion.tsx`.
 */

const MAX_BANDS = 4;

export async function FeatureBands() {
  let items;
  try {
    ({ items } = await listDestinations({ limit: 12 }));
  } catch {
    // One section failing must not blank the front page, as on Explore.
    return null;
  }

  const withPhotography = items
    .filter((destination) => (destination.media?.length ?? 0) > 0)
    .slice(0, MAX_BANDS);

  if (withPhotography.length === 0) return null;

  return (
    <section aria-labelledby="worth-seeing" className="space-y-16 sm:space-y-24">
      <Reveal from="none">
        <h2
          id="worth-seeing"
          className="font-display text-3xl font-bold tracking-tight sm:text-4xl"
        >
          Worth seeing
        </h2>
      </Reveal>

      {withPhotography.map((destination, position) => {
        const image = (destination.media.find((m) => m.is_primary) ??
          destination.media[0]) as GalleryImage;
        const imageFirst = position % 2 === 0;

        return (
          <Reveal
            key={destination.slug}
            as="article"
            from={imageFirst ? 'left' : 'right'}
            className="grid items-center gap-8 sm:grid-cols-2 sm:gap-12"
          >
            <figure className={imageFirst ? 'sm:order-1' : 'sm:order-2'}>
              <div className="overflow-hidden rounded-2xl bg-muted shadow-md">
                <img
                  src={mediaSrc(image.file_key)}
                  srcSet={mediaSrcSet(image.file_key)}
                  sizes="(min-width: 640px) 50vw, 100vw"
                  alt={image.alt_text}
                  width={image.width}
                  height={image.height}
                  loading="lazy"
                  decoding="async"
                  className="aspect-[4/3] w-full object-cover"
                />
              </div>
              <ImageCredit image={image} />
            </figure>

            <div className={imageFirst ? 'sm:order-2' : 'sm:order-1'}>
              <h3 className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
                {destination.name}
              </h3>
              {destination.summary ? (
                <p className="mt-4 text-lg leading-relaxed text-muted-foreground">
                  {destination.summary}
                </p>
              ) : null}
              <Link
                href={`/destinations/${destination.slug}`}
                className="mt-6 inline-flex items-center gap-1 text-sm font-semibold text-primary transition-colors duration-fast ease-out hover:text-primary/80"
              >
                Explore {destination.name}
                <span aria-hidden>→</span>
              </Link>
            </div>
          </Reveal>
        );
      })}
    </section>
  );
}

export function FeatureBandsSkeleton() {
  return (
    <div aria-hidden className="space-y-16">
      <div className="h-10 w-64 rounded-md bg-muted" />
      {Array.from({ length: 2 }).map((_, index) => (
        <div key={index} className="grid gap-8 sm:grid-cols-2 sm:gap-12">
          <div className="aspect-[4/3] w-full rounded-2xl bg-muted" />
          <div className="space-y-4">
            <div className="h-8 w-48 rounded bg-muted" />
            <div className="h-24 w-full rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
