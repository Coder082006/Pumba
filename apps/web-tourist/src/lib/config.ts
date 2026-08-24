import 'server-only';

import { apiFetch } from '@/lib/api';

/**
 * Where the base map comes from — ADR 0016, Appendix D9, SRS §24.1.
 *
 * Read from `GET /config`, which SRS §23.13 and §24.1 already specify as the
 * route delivering client configuration. The `map.tile_url` and
 * `map.tile_attribution` `system_setting` rows are therefore the single source
 * of truth, which is what makes changing tile provider an administrator action
 * rather than a redeployment of this application — the promise ADR 0016 makes
 * and that an environment variable here would have quietly broken.
 *
 * Fetched on the server, never in the browser: a commercial provider
 * authenticates with a key in the tile URL, and §30.9 forbids a secret
 * reaching source control or an image. A key in a client bundle is published
 * to every visitor.
 *
 * **There is deliberately no hardcoded fallback.** An unreachable `/config`
 * must not silently serve OpenStreetMap tiles, because OSM's usage policy does
 * not permit commercial production traffic — a fallback would turn an outage
 * into a licence breach that nobody notices. §24.1 names the correct
 * resilience mechanism instead: *"falling through to cached config"*. The
 * revalidating cache below is that mechanism, so a transient failure serves
 * the last known-good values and a sustained one is a visible error rather
 * than a quiet substitution.
 */
export interface MapConfig {
  tileUrl: string;
  attribution: string;
}

interface ConfigResponse {
  min_supported_version: string;
  enabled_currencies: string[];
  map_tile_url: string;
  map_tile_attribution: string;
  features: Record<string, boolean>;
}

/**
 * Five minutes. Long enough that a page render almost never blocks on this,
 * short enough that an administrator changing provider sees it take effect
 * without anyone being paged.
 */
const REVALIDATE_SECONDS = 300;

export async function mapConfig(): Promise<MapConfig> {
  const config = await apiFetch<ConfigResponse>('/config', {
    // `next` is Next.js's extension to RequestInit and is not in the DOM lib's
    // type, so it is attached here rather than widening `RequestOptions` for
    // one caller.
    next: { revalidate: REVALIDATE_SECONDS },
  } as Parameters<typeof apiFetch>[1]);

  return {
    tileUrl: config.map_tile_url,
    // Paired with the URL by the API, not reassembled here: attribution is a
    // licence term, and a client that could render one without the other
    // would be the bug ADR 0016 pairs them to prevent.
    attribution: config.map_tile_attribution,
  };
}
