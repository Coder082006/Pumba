/**
 * Following a keyset-paginated list to its end.
 *
 * Separate from `lib/catalogue` because that module is `server-only` and this
 * is not: it is arithmetic over cursors with no server dependency at all, and
 * it is the piece most worth testing directly. Putting it behind
 * `server-only` would have made it untestable for a reason unrelated to what
 * it does.
 */

import type { Page } from '@/lib/api';

/**
 * Every page of a list, followed to the end.
 *
 * The sitemap is the one caller that must not stop at the first page. §9.1's
 * `?limit` is bounded by the `page.max_size` setting, so "ask for all of them"
 * is not available — and a sitemap that silently listed only the first page
 * would look completely normal while hiding most of the catalogue from every
 * crawler. That is the §41.12 promise failing invisibly: a new destination
 * appears in the API and never in search results.
 *
 * `maxPages` is a stop, not a limit: a cursor that never returns `null` would
 * otherwise loop forever inside a page render. Reaching it means something is
 * wrong with the cursor rather than that the catalogue is large, so it throws
 * rather than returning a short list that would be indistinguishable from a
 * complete one.
 */
export async function listAll<T>(
  fetchPage: (cursor?: string) => Promise<Page<T>>,
  { maxPages = 50 }: { maxPages?: number } = {},
): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | undefined;

  for (let page = 0; page < maxPages; page += 1) {
    const result: Page<T> = await fetchPage(cursor);
    items.push(...result.items);
    if (result.nextCursor === null) return items;
    cursor = result.nextCursor;
  }

  throw new Error(
    `Pagination did not terminate after ${maxPages} pages. Returning a partial ` +
      'list here would be indistinguishable from a complete one.',
  );
}
