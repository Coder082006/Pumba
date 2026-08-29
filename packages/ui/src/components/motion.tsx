'use client';

import * as React from 'react';

import { cn } from '../lib/cn';

/**
 * Motion primitives — SRS §29 (NFR-P01, Accessibility ≥ 95).
 *
 * Two rules hold across everything here, and both are structural rather than
 * conventions somebody has to remember.
 *
 * **Only `transform` and `opacity` are animated.** Both are composited by the
 * browser and neither participates in layout, so an animation cannot move the
 * content around it. Animating height, margin or top is how a reveal becomes a
 * cumulative-layout-shift failure — and CI asserts `CLS < 0.1` on two real
 * pages, so that failure is a red build rather than a subtle nuisance.
 *
 * **Durations come from tokens.** `globals.css` flattens
 * `--duration-fast/base/slow` to 0.01ms under `prefers-reduced-motion`, which
 * disarms every animation built on them at the root. A component reaching past them for a
 * literal Tailwind duration opts itself out of that guarantee silently, and
 * `design-tokens.test.ts` fails the build for one.
 *
 * Reduced motion is not a preference to be styled around. Vestibular disorders
 * make large scroll-linked and auto-advancing movement a cause of nausea and
 * migraine, so the answer here is *less motion*, not *faster motion*.
 */

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';

function subscribeToMotionPreference(notify: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => {};
  const query = window.matchMedia(REDUCED_MOTION);
  query.addEventListener('change', notify);
  return () => query.removeEventListener('change', notify);
}

/**
 * `true` when the viewer has asked for reduced motion.
 *
 * `useSyncExternalStore` rather than `useState` + `useEffect`, and the
 * difference is not stylistic. The effect version reports `false` for the
 * first client render and corrects itself on the next — long enough for a
 * consumer's own effect to run once with the wrong answer and set up the
 * observer or timer it was supposed to skip. It then tears it down, so
 * nothing is visibly wrong and the bug is only findable by counting.
 *
 * This reads the media query during render on the client and returns `false`
 * for the server snapshot, so SSR and hydration agree and no consumer ever
 * sees the wrong value.
 */
export function usePrefersReducedMotion(): boolean {
  return React.useSyncExternalStore(
    subscribeToMotionPreference,
    () => typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION).matches,
    () => false,
  );
}

export interface RevealProps {
  children: React.ReactNode;
  /** Slide direction as it appears. `none` fades only. */
  from?: 'below' | 'left' | 'right' | 'none';
  /** Stagger, in milliseconds, for a run of siblings. */
  delayMs?: number;
  className?: string;
  as?: 'div' | 'section' | 'li' | 'article';
}

const OFFSET: Record<NonNullable<RevealProps['from']>, string> = {
  below: 'translate-y-6',
  left: '-translate-x-6',
  right: 'translate-x-6',
  none: '',
};

/**
 * Fades and slides its children in as they enter the viewport.
 *
 * **The honest limitation, stated because it is invisible otherwise:** the
 * initial state is `opacity-0`, so with JavaScript disabled the content stays
 * transparent. It remains in the DOM throughout — crawlers read it, screen
 * readers announce it, and §24.8's SEO surface is unaffected — but a sighted
 * visitor with scripting off would not see it. That is the cost of a
 * scroll-triggered reveal, and it is bounded to visual presentation.
 *
 * Under reduced motion the element is simply never hidden: no observer is
 * created and nothing animates.
 */
export function Reveal({
  children,
  from = 'below',
  delayMs = 0,
  className,
  as: Tag = 'div',
}: RevealProps) {
  const reduced = usePrefersReducedMotion();
  const [shown, setShown] = React.useState(false);
  const ref = React.useRef<HTMLElement | null>(null);

  React.useEffect(() => {
    if (reduced || typeof IntersectionObserver !== 'function') {
      setShown(true);
      return;
    }
    const element = ref.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShown(true);
            // Once only. Re-hiding on scroll-up makes a page feel unstable
            // and doubles the animation count for no gain.
            observer.disconnect();
          }
        }
      },
      // A little before the edge, so the movement finishes as the reader
      // arrives rather than starting under their eye.
      { rootMargin: '0px 0px -10% 0px', threshold: 0.05 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [reduced]);

  return (
    <Tag
      ref={ref as React.Ref<never>}
      className={cn(
        'transition-[opacity,transform] duration-slow ease-out motion-reduce:transition-none',
        shown ? 'translate-x-0 translate-y-0 opacity-100' : cn('opacity-0', OFFSET[from]),
        className,
      )}
      style={shown && delayMs ? undefined : { transitionDelay: `${delayMs}ms` }}
    >
      {children}
    </Tag>
  );
}

/**
 * One hero image, with its URLs **already resolved**.
 *
 * `Gallery` takes a `srcFor` function and builds its own URLs. This cannot:
 * it is a Client Component, and a function passed across the server/client
 * boundary is a build error — "Functions cannot be passed directly to Client
 * Components". Resolving on the server is the better arrangement anyway,
 * because §35.7's content-hashed keys and the CDN host are both server-side
 * concerns that a browser component has no business knowing.
 */
export interface HeroImage {
  /** Stable identity for React's key, not a URL. */
  file_key: string;
  src: string;
  srcSet?: string | undefined;
  alt_text: string;
  width: number;
  height: number;
}

export interface CrossfadeHeroProps {
  images: HeroImage[];
  /** How long each image is held. */
  intervalMs?: number;
  className?: string;
  children?: React.ReactNode;
  /** Accessible name for the region, e.g. "Zanzibar". */
  label: string;
}

/**
 * A hero that cross-fades through a place's photography.
 *
 * **This is not the hero carousel I argued against, and the difference is
 * built in rather than hoped for.**
 *
 * - Only the **first** image is eager and high priority; every other is lazy
 *   and arrives after paint. Largest Contentful Paint therefore measures one
 *   image, exactly as a static hero does.
 * - The container is a fixed height from the `h-hero` token, so nothing shifts
 *   whatever loads or fails. CLS is zero by construction, not by measurement.
 * - The fade is `opacity` and the drift is `transform`. Both composited;
 *   neither touches layout.
 *
 * **WCAG 2.2.2 changes the design, and it is worth saying why rather than
 * just complying.** Content that moves automatically for more than five
 * seconds needs a mechanism to pause it, so there is a real pause control —
 * not a decorative one. And under `prefers-reduced-motion` the cycle does not
 * merely slow down, it **never starts**: the first image is shown and the
 * controls disappear, because for a reader who gets migraines from moving
 * imagery a faster cycle is worse than a slower one, and the only safe amount
 * is none.
 *
 * The credit line is rendered by the caller through `children`, so this
 * component never has to know about licensing. That is not an excuse for it
 * to go unrendered: a hero is the largest use of somebody's photograph on the
 * site and the likeliest place for an attribution to quietly disappear, so
 * the caller is expected to pass one.
 */
export function CrossfadeHero({
  images,
  intervalMs = 6000,
  className,
  children,
  label,
}: CrossfadeHeroProps) {
  const reduced = usePrefersReducedMotion();
  const [index, setIndex] = React.useState(0);
  const [paused, setPaused] = React.useState(false);

  const cycling = images.length > 1 && !reduced && !paused;

  React.useEffect(() => {
    if (!cycling) return;
    const timer = window.setInterval(
      () => setIndex((current) => (current + 1) % images.length),
      intervalMs,
    );
    return () => window.clearInterval(timer);
  }, [cycling, images.length, intervalMs]);

  if (images.length === 0) {
    // A market with no photography yet. The box is still reserved, so the
    // page does not change shape the day pictures arrive.
    return (
      <section aria-label={label} className={cn('relative h-hero overflow-hidden bg-muted', className)}>
        {children}
      </section>
    );
  }

  return (
    <section
      aria-label={label}
      aria-roledescription={images.length > 1 ? 'image carousel' : undefined}
      className={cn('relative isolate h-hero overflow-hidden bg-muted', className)}
    >
      {images.map((image, position) => (
        <img
          key={image.file_key}
          src={image.src}
          srcSet={image.srcSet}
          sizes="100vw"
          alt={position === 0 ? image.alt_text : ''}
          // Only the first image is announced. The rest are the same subject
          // photographed differently, and a screen reader reading five
          // descriptions of a decorative backdrop is noise.
          aria-hidden={position === 0 ? undefined : true}
          width={image.width}
          height={image.height}
          loading={position === 0 ? 'eager' : 'lazy'}
          fetchPriority={position === 0 ? 'high' : 'low'}
          decoding="async"
          className={cn(
            'absolute inset-0 h-full w-full object-cover',
            // One transition declaration, covering both properties. Two
            // `transition-*` classes on the same element is a race the last
            // one wins, which is how a fade quietly stops fading.
            'transition-[opacity,transform] duration-slow ease-in-out',
            position === index ? 'opacity-100' : 'opacity-0',
            // A slow drift while this image is the visible one. Scale rather
            // than position, so nothing around it can move.
            cycling && position === index ? 'scale-105' : 'scale-100',
          )}
        />
      ))}

      {/* A scrim, so headline text over an unknown photograph is always
          readable. Contrast on a hero cannot be checked at build time — the
          image changes — so it is guaranteed structurally instead. */}
      <div
        aria-hidden
        className="absolute inset-0 bg-gradient-to-t from-foreground/70 via-foreground/25 to-transparent"
      />

      <div className="relative z-10 flex h-full flex-col justify-end">{children}</div>

      {images.length > 1 && !reduced ? (
        <div className="absolute bottom-4 right-4 z-20 flex items-center gap-3">
          <div className="flex gap-1.5">
            {images.map((image, position) => (
              <button
                key={image.file_key}
                type="button"
                aria-label={`Show image ${position + 1} of ${images.length}`}
                aria-current={position === index}
                onClick={() => setIndex(position)}
                className={cn(
                  'h-2 w-2 rounded-full transition-opacity duration-fast ease-out',
                  'bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-background',
                  position === index ? 'opacity-100' : 'opacity-50 hover:opacity-75',
                )}
              />
            ))}
          </div>
          {/* WCAG 2.2.2. Not decorative: this is the required mechanism. */}
          <button
            type="button"
            onClick={() => setPaused((was) => !was)}
            aria-pressed={paused}
            className="rounded-md bg-background/85 px-2 py-1 text-xs font-medium text-foreground transition-colors duration-fast ease-out hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-background"
          >
            {paused ? 'Play' : 'Pause'}
          </button>
        </div>
      ) : null}
    </section>
  );
}
