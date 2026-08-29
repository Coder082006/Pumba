/**
 * `mediaSrcSet` names files that `fetch_commons_media.py` actually writes.
 *
 * The narrow variant convention — `<stem>-960.webp` beside `<stem>.webp` —
 * exists in two places by necessity: the Python script that produces the
 * files, and this TypeScript that requests them. Nothing in either language
 * can see the other.
 *
 * The failure if they drift is the quiet kind. A `srcset` entry pointing at a
 * file that does not exist is a 404 the browser swallows: it falls back to
 * `src` and the page looks perfect, while every phone downloads a
 * hero-sized image and the mobile LCP budget goes with it. No error, no
 * console warning, nothing to notice.
 *
 * So the convention is asserted from both ends: here, that the names are
 * built as agreed, and in `apps/api/tests/test_media_seed.py`, that a file
 * with each of those names is actually on disk for every seeded row.
 */

import { describe, expect, it } from 'vitest';

import { mediaSrc, mediaSrcSet } from '@/components/catalogue/cards';

describe('mediaSrcSet', () => {
  it('offers the narrow variant and the wide original, with widths', () => {
    expect(mediaSrcSet('97b52eed51dc7a8d.webp')).toBe(
      '/media/97b52eed51dc7a8d-960.webp 960w, /media/97b52eed51dc7a8d.webp 1600w',
    );
  });

  it('names the narrow file exactly as the fetch script writes it', () => {
    // `scripts/fetch_commons_media.py` writes `<stem>{suffix}.webp` for
    // suffix in ('', '-960'). This is that rule, restated in the only other
    // place that has to know it.
    const key = 'a74047bdfd02a5b4.webp';
    const narrow = key.replace('.webp', '-960.webp');
    expect(mediaSrcSet(key)).toContain(mediaSrc(narrow));
  });

  it('returns undefined for a key that is not a .webp', () => {
    // Degrades to the single `src` rather than requesting a variant nobody
    // generated. A hand-added JPEG should render, not 404 twice.
    expect(mediaSrcSet('legacy-photo.jpg')).toBeUndefined();
    expect(mediaSrcSet('img/9f8e7d6c')).toBeUndefined();
  });

  it('honours the configured media base for both entries', () => {
    // §35.7 puts these behind a CDN in production, so neither entry may
    // hard-code the origin. Both go through `mediaSrc`.
    const set = mediaSrcSet('abc.webp');
    expect(set).not.toBeUndefined();
    for (const entry of set!.split(',')) {
      expect(entry.trim().startsWith(mediaSrc(''))).toBe(true);
    }
  });
});
