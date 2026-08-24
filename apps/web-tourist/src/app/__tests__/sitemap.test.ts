/**
 * The sitemap, and the three ways it can be wrong while looking right.
 *
 *   - listing only the first page of each kind (`listAll`, tested separately);
 *   - advertising a URL that 404s;
 *   - serving an empty document during an outage, which tells a crawler the
 *     site has no pages and invites it to drop the ones it already knows.
 *
 * None of those produce an error or an odd-looking file. The last is the worst
 * because it is indistinguishable from a genuinely empty catalogue.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const listDestinations = vi.fn();
const listAttractions = vi.fn();
const listActivities = vi.fn();

// The real `listAll` runs — the walk is part of what is under test here, and
// stubbing it would let a sitemap that reads one page pass.
vi.mock('@/lib/catalogue', () => ({
  listDestinations: (...args: unknown[]) => listDestinations(...args),
  listAttractions: (...args: unknown[]) => listAttractions(...args),
  listActivities: (...args: unknown[]) => listActivities(...args),
}));

const onePage = (slugs: string[]) => () =>
  Promise.resolve({ items: slugs.map((slug) => ({ slug })), nextCursor: null });

async function render() {
  const { default: sitemap } = await import('@/app/sitemap');
  return sitemap();
}

beforeEach(() => {
  vi.resetModules();
  listDestinations.mockImplementation(onePage(['nungwi', 'stone-town']));
  listAttractions.mockImplementation(onePage(['prison-island']));
  listActivities.mockImplementation(onePage(['spice-tour']));
});

describe('what the sitemap lists', () => {
  it('carries every row of every kind, under its own route segment', async () => {
    const urls = (await render()).map((entry) => entry.url);
    expect(urls).toEqual(
      expect.arrayContaining([
        expect.stringContaining('/destinations/nungwi'),
        expect.stringContaining('/destinations/stone-town'),
        expect.stringContaining('/attractions/prison-island'),
        expect.stringContaining('/activities/spice-tour'),
      ]),
    );
  });

  it('includes the browsable entry points', async () => {
    const urls = (await render()).map((entry) => entry.url);
    expect(urls.some((url) => url.endsWith('/explore'))).toBe(true);
  });

  it('lists no accommodation, because no property has a page to point at', async () => {
    // ADR 0013 deferred §24.12 and §24.13. A sitemap entry for a URL that
    // 404s is worse than an omission — it teaches a crawler that this
    // document is unreliable.
    const urls = (await render()).map((entry) => entry.url);
    expect(urls.some((url) => url.includes('/accommodation'))).toBe(false);
    expect(urls.some((url) => url.includes('/stays'))).toBe(false);
  });

  it('claims no lastModified, rather than claiming everything changed today', async () => {
    // The public serializers carry no `updated_at`. Stamping `new Date()`
    // would be a signal search engines learn to distrust.
    for (const entry of await render()) {
      expect(entry.lastModified).toBeUndefined();
    }
  });
});

describe('when the catalogue cannot be read', () => {
  it('refuses to serve a sitemap that omits every section', async () => {
    // The invisible failure. Two static entries and no destinations is a
    // well-formed document asserting the site has nothing on it.
    const down = () => Promise.reject(new Error('API unreachable'));
    listDestinations.mockImplementation(down);
    listAttractions.mockImplementation(down);
    listActivities.mockImplementation(down);

    await expect(render()).rejects.toThrow(/refusing to serve/);
  });

  it('still serves the rest when one section fails', async () => {
    // Degraded rather than absent: a 500 here removes every page from the
    // crawler's view, not just the broken section's.
    listActivities.mockImplementation(() => Promise.reject(new Error('API unreachable')));

    const urls = (await render()).map((entry) => entry.url);
    expect(urls.some((url) => url.includes('/destinations/nungwi'))).toBe(true);
    expect(urls.some((url) => url.includes('/activities/'))).toBe(false);
  });

  it('serves an empty catalogue as empty, which is not the same failure', async () => {
    // Guards the guard above: "every section returned nothing" must still be
    // servable when the sections actually succeeded.
    listDestinations.mockImplementation(onePage([]));
    listAttractions.mockImplementation(onePage([]));
    listActivities.mockImplementation(onePage([]));

    const urls = (await render()).map((entry) => entry.url);
    expect(urls.some((url) => url.endsWith('/explore'))).toBe(true);
  });
});
