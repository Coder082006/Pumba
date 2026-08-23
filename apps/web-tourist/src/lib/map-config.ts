import 'server-only';

/**
 * Where the base map comes from — ADR 0016, Appendix D9.
 *
 * Read on the server at request time and passed to `<Map>` as props, never
 * inlined into the client bundle. That matters for the case ADR 0016 names:
 * a commercial provider authenticates with a key in the tile URL, and §30.9
 * forbids a secret reaching source control or an image — a key baked into a
 * Next.js build is published to every visitor of every page.
 *
 * **Known gap: this is a second source of truth, and it should not stay one.**
 * The authoritative values are the `map.tile_url` and `map.tile_attribution`
 * `system_setting` rows, which is what makes changing provider an
 * administrator action rather than a deployment. Nothing yet serves those rows
 * to the web client, so these environment variables mirror their defaults and
 * an administrator changing the rows would not move this client. Closing it
 * means a public configuration endpoint exposing the client-visible settings —
 * which needs a decision about *which* settings are public, and that is a
 * backend change rather than something to invent inside a web commit. Until
 * then the deployment must set both halves together.
 */
export interface MapConfig {
  tileUrl: string;
  attribution: string;
}

/**
 * The same development defaults the settings register carries. OpenStreetMap's
 * tile usage policy does not permit commercial production traffic, so a
 * deployment that has not set these is not ready to launch (ADR 0016).
 */
const DEVELOPMENT_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const DEVELOPMENT_ATTRIBUTION = '© OpenStreetMap contributors';

export function mapConfig(): MapConfig {
  return {
    tileUrl: process.env.MAP_TILE_URL ?? DEVELOPMENT_TILE_URL,
    // Paired with the URL deliberately: attribution is a licence term of every
    // provider worth using, and a deployment that changes one without the
    // other is in breach rather than merely untidy.
    attribution: process.env.MAP_TILE_ATTRIBUTION ?? DEVELOPMENT_ATTRIBUTION,
  };
}
