'use client';

import * as React from 'react';

import { cn } from '../lib/cn';

/**
 * A base map with pins — ADR 0016, SRS §29 (NFR-P01).
 *
 * **The container reserves its box before any script runs.** The wrapper is
 * server-rendered with an explicit aspect ratio and MapLibre is imported
 * dynamically into it on mount. A map that mounts into a zero-height div and
 * then expands is the single easiest way to fail §29's `CLS < 0.1` gate, which
 * commit 34 asserts in CI — and it is invisible in development, where the
 * bundle is warm and the shift happens before anyone looks.
 *
 * **No vendor is named here.** ADR 0016 puts the tile URL and its attribution
 * in `system_setting` rows, so both arrive as props from whatever loaded the
 * page. Hardcoding a default would defeat the arrangement — D9 is unresolved,
 * and the development default (OpenStreetMap) may not serve commercial
 * production traffic.
 *
 * **Attribution is not optional.** It is a licence term of every tile provider
 * worth using, so it is rendered from the same prop pair as the URL rather
 * than left to each caller to remember. `attribution` is required for that
 * reason: a caller that has a URL has an attribution.
 *
 * **Tile failures are asynchronous, so a try/catch cannot see them.** The
 * first version wrapped `import()` and `new Map()` in a try/catch and set the
 * fallback from it — but tiles are fetched *after* construction resolves, so
 * the one failure the fallback exists for was the one it could never catch. A
 * dead tile host produced an unhandled rejection and a raw stack trace instead
 * of the panel below. MapLibre reports those on its `error` event, which is
 * where the fallback is now driven from.
 *
 * A single tile 404 must not blank a working map, so the fallback fires only
 * while no tile has ever loaded — `sourcedata` records that, and after it the
 * map keeps whatever it has.
 *
 * **The effect keys on values, not on object identity.** `pins` and `center`
 * are literals at every call site (`pins={[{…}]}`), so a dependency on them
 * re-ran this effect on every render: the map was destroyed and rebuilt, and
 * every visible tile re-requested, continuously. That is a performance defect
 * on its own and a good way to be rate-limited by a tile provider whose usage
 * policy one is already stretching.
 */
export interface MapPin {
  /** Stable identity, so React can keep a marker across a re-render. */
  id: string;
  latitude: number;
  longitude: number;
  /** Announced to assistive technology; a marker with no name is a dot. */
  label: string;
}

export interface MapProps {
  /** Tile URL template from `map.tile_url`. */
  tileUrl: string;
  /** Attribution text from `map.tile_attribution`. A licence term, not a credit. */
  attribution: string;
  pins: MapPin[];
  /** Where to centre when there is nothing to fit. Ignored once pins exist. */
  center?: { latitude: number; longitude: number } | undefined;
  zoom?: number | undefined;
  /**
   * Reserved box, as a CSS aspect-ratio. Set on the server-rendered wrapper,
   * so changing it changes the space held before hydration.
   */
  aspectRatio?: string | undefined;
  /** Announced as the region's name. */
  title?: string | undefined;
  className?: string | undefined;
}

export function Map({
  tileUrl,
  attribution,
  pins,
  center,
  zoom = 11,
  aspectRatio = '16 / 9',
  title = 'Map',
  className,
}: MapProps) {
  const holder = React.useRef<HTMLDivElement>(null);
  const [failed, setFailed] = React.useState(false);

  // Read inside the effect so the effect can depend on the *values* below
  // rather than on the identity of a freshly-built array each render.
  const latest = React.useRef({ pins, center });
  latest.current = { pins, center };

  // Cheap, stable keys. `pins` changing content genuinely should rebuild the
  // markers; `pins` merely being a new array with the same contents should not.
  const pinKey = pins.map((p) => `${p.id}:${p.latitude}:${p.longitude}:${p.label}`).join('|');
  const centerKey = center ? `${center.latitude},${center.longitude}` : '';

  React.useEffect(() => {
    if (holder.current === null) return;
    const node = holder.current;
    let map: { remove: () => void } | null = null;
    let cancelled = false;

    // Dynamic so MapLibre and its stylesheet stay out of the initial bundle.
    // A page that shows a map below the fold should not pay for it in LCP.
    void (async () => {
      try {
        const maplibre = await import('maplibre-gl');
        if (cancelled) return;

        const { pins: currentPins, center: currentCenter } = latest.current;

        const instance = new maplibre.Map({
          container: node,
          style: {
            version: 8,
            sources: {
              base: {
                type: 'raster',
                tiles: [tileUrl],
                tileSize: 256,
                attribution,
              },
            },
            layers: [{ id: 'base', type: 'raster', source: 'base' }],
          },
          center: [
            currentCenter?.longitude ?? currentPins[0]?.longitude ?? 0,
            currentCenter?.latitude ?? currentPins[0]?.latitude ?? 0,
          ],
          zoom,
          // The tourist reads this map; they do not author it. Rotation makes
          // a pinch gesture lose north on a phone, which is disorienting on
          // the one screen whose job is telling someone where they are.
          pitchWithRotate: false,
          dragRotate: false,
        });
        map = instance;

        // Tiles load after this point, so their failures never reached the
        // try/catch below. A transient DNS failure or a blocked tile host
        // showed the user an unhandled rejection instead of the panel.
        let anyTileLoaded = false;
        instance.on('sourcedata', (event: { sourceId?: string; isSourceLoaded?: boolean }) => {
          if (event.sourceId === 'base' && event.isSourceLoaded) anyTileLoaded = true;
        });
        instance.on('error', () => {
          // Only while nothing has rendered. One missing tile at the edge of
          // coverage must not replace a map the tourist is already reading.
          if (!cancelled && !anyTileLoaded) setFailed(true);
        });

        for (const pin of currentPins) {
          const marker = new maplibre.Marker()
            .setLngLat([pin.longitude, pin.latitude])
            .addTo(instance);
          // `role="img"` before `aria-label`, and the order matters more than
          // it looks. MapLibre's marker is a bare `<div>`, and `aria-label` on
          // an element with no role is *prohibited* by ARIA — assistive
          // technology is entitled to ignore it, so the label was both an
          // accessibility failure and doing nothing. Lighthouse's
          // `aria-prohibited-attr` is what caught it.
          const element = marker.getElement();
          element.setAttribute('role', 'img');
          element.setAttribute('aria-label', pin.label);
          element.setAttribute('title', pin.label);
        }

        if (currentPins.length > 1) {
          const bounds = new maplibre.LngLatBounds();
          for (const pin of currentPins) bounds.extend([pin.longitude, pin.latitude]);
          instance.fitBounds(bounds, { padding: 48, animate: false });
        }
      } catch {
        // Construction failed outright — the dynamic import was blocked, or
        // WebGL is unavailable. Tile failures are handled by the `error`
        // listener above, not here.
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      map?.remove();
    };
    // `pinKey` and `centerKey` stand in for `pins` and `center`, which are new
    // objects on every render at every call site — the values are what should
    // re-run this, never the identities. `latest` supplies the objects.
    //
    // No `eslint-disable` here: `react-hooks/exhaustive-deps` is not
    // configured in this workspace at all, so the directive would name a rule
    // that does not exist and fail the lint that is configured. Enabling the
    // plugin across the workspace is worth doing and is a separate change —
    // this deliberate omission is the first thing it would flag.
  }, [tileUrl, attribution, pinKey, centerKey, zoom]);

  return (
    <figure className={cn('relative overflow-hidden rounded-lg bg-muted', className)}>
      {/* The reserved box. Rendered on the server with its ratio already set,
          so hydration adds pixels inside it and never changes its height. */}
      <div
        ref={holder}
        role="region"
        aria-label={title}
        style={{ aspectRatio }}
        className="w-full"
      />
      {failed ? (
        <p className="absolute inset-0 flex items-center justify-center p-4 text-center text-sm text-muted-foreground">
          The map could not be loaded. Locations are listed below.
        </p>
      ) : null}
      <figcaption className="px-2 py-1 text-right text-[11px] leading-tight text-muted-foreground">
        {attribution}
      </figcaption>
    </figure>
  );
}
