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
  center?: { latitude: number; longitude: number };
  zoom?: number;
  /**
   * Reserved box, as a CSS aspect-ratio. Set on the server-rendered wrapper,
   * so changing it changes the space held before hydration.
   */
  aspectRatio?: string;
  /** Announced as the region's name. */
  title?: string;
  className?: string;
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
          center: [center?.longitude ?? pins[0]?.longitude ?? 0, center?.latitude ?? pins[0]?.latitude ?? 0],
          zoom,
          // The tourist reads this map; they do not author it. Rotation makes
          // a pinch gesture lose north on a phone, which is disorienting on
          // the one screen whose job is telling someone where they are.
          pitchWithRotate: false,
          dragRotate: false,
        });
        map = instance;

        for (const pin of pins) {
          const marker = new maplibre.Marker()
            .setLngLat([pin.longitude, pin.latitude])
            .addTo(instance);
          marker.getElement().setAttribute('aria-label', pin.label);
          marker.getElement().setAttribute('title', pin.label);
        }

        if (pins.length > 1) {
          const bounds = new maplibre.LngLatBounds();
          for (const pin of pins) bounds.extend([pin.longitude, pin.latitude]);
          instance.fitBounds(bounds, { padding: 48, animate: false });
        }
      } catch {
        // A blocked or failed tile host must not take the page down with it.
        // The reserved box stays, so nothing reflows; it just shows the
        // fallback instead.
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      map?.remove();
    };
  }, [tileUrl, attribution, pins, center, zoom]);

  return (
    <figure className={cn('relative overflow-hidden rounded-lg bg-slate-100', className)}>
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
        <p className="absolute inset-0 flex items-center justify-center p-4 text-center text-sm text-slate-600">
          The map could not be loaded. Locations are listed below.
        </p>
      ) : null}
      <figcaption className="px-2 py-1 text-right text-[11px] leading-tight text-slate-500">
        {attribution}
      </figcaption>
    </figure>
  );
}
