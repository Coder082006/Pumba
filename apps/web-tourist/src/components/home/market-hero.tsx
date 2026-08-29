import Link from 'next/link';
import { CrossfadeHero, ImageCredit, type GalleryImage, type HeroImage } from '@pumba/ui';

import { mediaSrc, mediaSrcSet } from '@/components/catalogue/cards';
import { listMarkets } from '@/lib/catalogue';

/**
 * The landing page hero — SRS §24.6, ADR 0018.
 *
 * **The photographs are the market's, read from the API.** Nothing here maps
 * "Zanzibar → a beach" in code; §4.2 forbids exactly that, and it is also the
 * whole point of the market tier. Opening Arusha means creating a row and
 * attaching pictures, and this component then shows Arusha's waterfalls
 * without being touched.
 *
 * **It degrades in the two ways that matter.** A market with no photography
 * yet renders the reserved box with its headline over a token gradient — the
 * page keeps its shape for the day pictures arrive. And if `/markets` is
 * unreachable the hero renders without imagery rather than failing the page:
 * the front door of a tourism product should not 500 because a gallery query
 * timed out.
 *
 * The credit is rendered here rather than inside `CrossfadeHero`, so the
 * primitive never has to know about licensing — but it is rendered, because a
 * hero is the largest use of somebody's photograph on the site and the
 * likeliest place for an attribution to quietly go missing.
 */

/** The first market that is actually open, which is whose pictures we show. */
async function openMarket() {
  try {
    const markets = await listMarkets();
    return markets.find((market) => market.is_open) ?? null;
  } catch {
    return null;
  }
}

export async function MarketHero() {
  const market = await openMarket();
  const gallery = (market?.media ?? []) as GalleryImage[];
  const credited = gallery[0];

  // URLs resolved here, on the server. `CrossfadeHero` is a Client Component
  // and a function cannot cross that boundary — and §35.7's content-hashed
  // keys and the CDN host are server-side concerns regardless.
  const images: HeroImage[] = gallery.map((image) => ({
    file_key: image.file_key,
    src: mediaSrc(image.file_key),
    srcSet: mediaSrcSet(image.file_key),
    alt_text: image.alt_text,
    width: image.width,
    height: image.height,
  }));

  return (
    <CrossfadeHero
      images={images}
      label={market?.name ?? 'Pumba'}
      className="rounded-2xl border border-border shadow-sm"
    >
      <div className="p-6 pb-14 sm:p-12 sm:pb-16">
        {market ? (
          <p className="text-sm font-semibold uppercase tracking-widest text-background/90">
            {market.name}
          </p>
        ) : null}
        <h1 className="mt-4 max-w-3xl font-display text-4xl font-bold leading-[1.1] tracking-tight text-background sm:text-6xl">
          Plan the whole journey before you travel.
        </h1>
        <p className="mt-6 max-w-xl text-lg leading-relaxed text-background/90">
          {market?.summary ??
            'Browse destinations, pick the things worth doing, and build the days around where you are staying — transfers included.'}
        </p>
        <div className="mt-10 flex flex-wrap gap-3">
          {/* §24.6's "prominent Plan a Trip button". It points at Explore
              rather than at a planner: the Trip Planner is §24.14 and belongs
              to the trip module, and a button leading to a 404 is the defect
              `navigation.test.ts` fails the build for. */}
          <Link
            href="/explore"
            className="rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground shadow-md transition-colors duration-fast ease-out hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-background focus-visible:ring-offset-2"
          >
            Start exploring
          </Link>
          <Link
            href="/stays"
            className="rounded-md border border-background/40 bg-background/10 px-6 py-3 text-sm font-semibold text-background backdrop-blur transition-colors duration-fast ease-out hover:bg-background/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-background"
          >
            Where are you staying?
          </Link>
        </div>

        {credited ? (
          <ImageCredit image={credited} className="mt-6 text-background/70" />
        ) : null}
      </div>
    </CrossfadeHero>
  );
}

/** The reserved box, while the market query is in flight. */
export function MarketHeroSkeleton() {
  return (
    <div
      aria-hidden
      className="h-hero rounded-2xl border border-border bg-gradient-to-br from-primary/10 via-muted to-accent/10"
    />
  );
}
