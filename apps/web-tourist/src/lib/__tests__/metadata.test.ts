/**
 * Every catalogue page carries a description — SRS §24.8.
 *
 * The rule this file exists to hold is one line long and was broken on all
 * three pages at once: `subject.summary ?? undefined` produces no meta
 * description whenever the summary is null, and the failure branch of
 * `generateMetadata` produced none either. Lighthouse's SEO gate found it on
 * `/destinations/stone-town`, where `meta-description` scored 0 on a page that
 * was otherwise a clean 200 with full marks for performance and accessibility.
 *
 * The tests below check the two things that actually matter — that a
 * description always exists, and that it never claims to know something the
 * platform does not — rather than the exact wording, which is copy and should
 * be free to change without a failing build.
 */

import { describe, expect, it } from 'vitest';

import { fallbackDescription, type CatalogueEntity } from '../metadata';

const ENTITIES: CatalogueEntity[] = ['destination', 'attraction', 'activity'];

describe('fallbackDescription', () => {
  it.each(ENTITIES)('gives %s a usable description with a name', (entity) => {
    const text = fallbackDescription(entity, 'Stone Town');
    expect(text).toContain('Stone Town');
    // Lighthouse wants a description that says something. The exact length
    // matters less than it not being a stub, so this is a floor rather than
    // an assertion about the copy.
    expect(text.length).toBeGreaterThan(40);
  });

  it.each(ENTITIES)('gives %s a usable description without a name', (entity) => {
    // The failure path: the fetch that would have supplied the name is the
    // one that failed, so every branch has to read correctly without it.
    const text = fallbackDescription(entity);
    expect(text.length).toBeGreaterThan(40);
    expect(text).not.toContain('undefined');
    expect(text).not.toContain('null');
  });

  it('never leaves a dangling preposition when the name is missing', () => {
    // `…places to stay in , with…` is the specific way this breaks, and it
    // reaches search results rather than a log.
    for (const entity of ENTITIES) {
      expect(fallbackDescription(entity)).not.toMatch(/\s(in|at|for)\s*[,.]/);
    }
  });

  it('describes the page rather than the place', () => {
    /**
     * The line between a fallback and a fabrication.
     *
     * A generic sentence about what the page contains is honest when the
     * summary is missing. A generic sentence about what the *place is like*
     * would be invention, and would be published to search engines under the
     * platform's name — the same reason the destination page's JSON-LD omits
     * `description` entirely rather than filling it in.
     */
    const text = fallbackDescription('destination', 'Stone Town');
    for (const claim of ['beautiful', 'stunning', 'popular', 'best', 'famous', 'must-see']) {
      expect(text.toLowerCase()).not.toContain(claim);
    }
  });

  it('distinguishes the three page kinds', () => {
    // One shared sentence across all three would be a duplicate-description
    // problem across most of the catalogue, which is its own SEO defect.
    const texts = ENTITIES.map((entity) => fallbackDescription(entity, 'Stone Town'));
    expect(new Set(texts).size).toBe(ENTITIES.length);
  });
});
