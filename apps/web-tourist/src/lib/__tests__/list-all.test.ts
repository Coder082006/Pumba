/**
 * `listAll` — the pagination walk behind the sitemap.
 *
 * The failure it exists to prevent is silent: `?limit` is bounded by
 * `page.max_size`, so a sitemap built from one call would list the first page
 * of each kind and look completely normal. Every page past the first would be
 * invisible to every crawler, and §41.12's "publish a destination and it is
 * found" would be false for most of the catalogue with nothing reporting it.
 */

import { describe, expect, it, vi } from 'vitest';

import { listAll } from '@/lib/paginate';
import type { Page } from '@/lib/api';

function pages<T>(...batches: T[][]): (cursor?: string) => Promise<Page<T>> {
  return (cursor?: string) => {
    const index = cursor === undefined ? 0 : Number(cursor);
    const items = batches[index] ?? [];
    const last = index >= batches.length - 1;
    return Promise.resolve({ items, nextCursor: last ? null : String(index + 1) });
  };
}

describe('listAll', () => {
  it('follows the cursor to the end and returns every row in order', () => {
    return expect(listAll(pages([1, 2], [3, 4], [5]))).resolves.toEqual([1, 2, 3, 4, 5]);
  });

  it('sends no cursor on the first call and the server’s cursor after', async () => {
    // The cursor is opaque (§9.1) and the server refuses one replayed against
    // a different ordering, so it must be passed back verbatim and never
    // constructed.
    const fetchPage = vi.fn(pages(['a'], ['b']));
    await listAll(fetchPage);
    expect(fetchPage.mock.calls).toEqual([[undefined], ['1']]);
  });

  it('stops at a single page when there is no next cursor', async () => {
    const fetchPage = vi.fn(pages(['only']));
    await listAll(fetchPage);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });

  it('returns an empty list for an empty catalogue', () => {
    return expect(listAll(pages([]))).resolves.toEqual([]);
  });

  it('throws rather than truncating when the cursor never terminates', async () => {
    // A partial list would be indistinguishable from a complete one, which is
    // the whole problem. Better a visible error than a sitemap that is quietly
    // missing four fifths of the catalogue.
    const endless = () => Promise.resolve({ items: ['x'], nextCursor: 'always' });
    await expect(listAll(endless, { maxPages: 3 })).rejects.toThrow(/did not terminate/);
  });

  it('propagates a failure rather than returning what it collected so far', async () => {
    // Same reasoning: `collect` in the sitemap decides what a failure means.
    // Swallowing it here would hand that decision a short list and no way to
    // know it was short.
    const failing = (cursor?: string) =>
      cursor === undefined
        ? Promise.resolve({ items: ['a'], nextCursor: '1' })
        : Promise.reject(new Error('upstream'));
    await expect(listAll(failing)).rejects.toThrow('upstream');
  });
});
