/**
 * Every internal link in the persistent shell resolves to a route.
 *
 * This exists because three of the four header links did not. `/destinations`,
 * `/attractions` and `/activities` were written in commit 30 alongside the
 * shell; commits 31 and 32 then built `/destinations/[slug]`,
 * `/attractions/[slug]` and `/activities/[slug]` — the detail pages — and no
 * index route was ever added. The header shipped pointing at three 404s.
 *
 * Nothing caught it, and nothing would have. The header renders correctly, its
 * tests pass, every page it appears on builds, and `next build` lists the
 * routes that exist without noticing that something links to routes that do
 * not. It only surfaces when a person clicks — and the developer who wrote the
 * link is the last person to click it.
 *
 * So the check is against the filesystem router rather than against a list
 * kept by hand: a hand-kept list of valid routes would need updating in the
 * same commit that adds a page, which is the same discipline that failed here.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { describe, expect, it } from 'vitest';

const SRC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const APP_DIR = join(SRC_DIR, 'app');

/**
 * The App Router's route table, derived the way Next derives it.
 *
 * A directory holding `page.tsx` is a route. `(group)` segments are
 * organisational and contribute no path. `[slug]` segments match anything, so
 * they become a prefix that any deeper path satisfies.
 */
function routes(dir: string, prefix = ''): { exact: Set<string>; dynamic: string[] } {
  const exact = new Set<string>();
  const dynamic: string[] = [];

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isFile() && entry.name === 'page.tsx') {
      exact.add(prefix === '' ? '/' : prefix);
      continue;
    }
    if (!entry.isDirectory()) continue;

    const isGroup = entry.name.startsWith('(') && entry.name.endsWith(')');
    const isDynamic = entry.name.startsWith('[');
    const nested = routes(
      join(dir, entry.name),
      isGroup ? prefix : `${prefix}/${isDynamic ? '*' : entry.name}`,
    );

    for (const route of nested.exact) {
      if (route.includes('/*')) dynamic.push(route.slice(0, route.indexOf('/*')));
      else exact.add(route);
    }
    dynamic.push(...nested.dynamic);
  }

  return { exact, dynamic };
}

/**
 * Every literal internal `href="…"` under `src`.
 *
 * Only the literal form. A computed href — `href={`/destinations/${slug}`}` —
 * is checked by the type system on one end and by the API on the other, and
 * matching it here would mean evaluating template literals from source text.
 * The links that rot are the hand-written constants anyway: nothing recomputes
 * them when a route is renamed.
 */
function staticLinks(dir: string): { file: string; href: string }[] {
  const found: { file: string; href: string }[] = [];

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === '__tests__') continue;
      found.push(...staticLinks(path));
      continue;
    }
    if (!entry.name.endsWith('.tsx')) continue;

    for (const match of readFileSync(path, 'utf8').matchAll(/href="([^"]+)"/g)) {
      const href = match[1];
      // External destinations and fragments are not this router's business.
      if (!href || !href.startsWith('/')) continue;
      found.push({ file: entry.name, href });
    }
  }
  return found;
}

describe('the application', () => {
  it('links only to routes that exist', () => {
    const { exact, dynamic } = routes(APP_DIR);
    const links = staticLinks(SRC_DIR);

    // Guards the guard: an empty link list, or an empty route table, would
    // make the assertion below pass while checking nothing at all.
    expect(links.length).toBeGreaterThan(3);
    expect(exact.has('/')).toBe(true);

    const broken = links.filter(({ href }) => {
      const path = href.split('?')[0]?.replace(/\/$/, '') || '/';
      return !exact.has(path) && !dynamic.some((prefix) => path.startsWith(`${prefix}/`));
    });

    expect(broken).toEqual([]);
  });
});

/**
 * Every `<ConsentCheckbox document={{ … }}>` call site, with its href if it
 * declares one.
 *
 * Matched from source rather than by rendering, because the failure being
 * prevented is a *build-time* one: a call site that names a route which does
 * not exist must not survive to a running page, where the only thing that
 * finds it is a user clicking during registration.
 */
function consentDocuments(dir: string): { file: string; href: string | null }[] {
  const found: { file: string; href: string | null }[] = [];

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === '__tests__') continue;
      found.push(...consentDocuments(path));
      continue;
    }
    if (!entry.name.endsWith('.tsx')) continue;

    const source = readFileSync(path, 'utf8');
    for (const match of source.matchAll(/<ConsentCheckbox[\s\S]*?\/>/g)) {
      const usage = match[0];
      // The component's own definition contains the identifier but no call.
      if (!usage.includes('document=')) continue;
      const href = /href:\s*'([^']+)'/.exec(usage);
      found.push({ file: entry.name, href: href?.[1] ?? null });
    }
  }
  return found;
}

/** Required checkboxes written by hand rather than through the component. */
function handRolledConsent(dir: string): string[] {
  const found: string[] = [];

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === '__tests__') continue;
      found.push(...handRolledConsent(path));
      continue;
    }
    if (!entry.name.endsWith('.tsx')) continue;
    if (entry.name === 'consent-checkbox.tsx') continue;

    const source = readFileSync(path, 'utf8');
    // A checkbox whose surrounding label carries a link is the shape that
    // failed: the control and its document, coupled by nothing.
    for (const match of source.matchAll(/<label[\s\S]*?<\/label>/g)) {
      const block = match[0];
      if (block.includes('type="checkbox"') && block.includes('<Link')) {
        found.push(entry.name);
      }
    }
  }
  return found;
}

describe('consent controls', () => {
  it('never name a route that does not exist', () => {
    // The specific failure: registration's required "I accept the terms of
    // use" checkbox linked to `/terms`, which was a 404. The form would not
    // submit until it was ticked, so a user had to agree to a document the
    // application could not show them.
    const { exact, dynamic } = routes(APP_DIR);
    const usages = consentDocuments(SRC_DIR);

    expect(usages.length).toBeGreaterThan(0);

    const broken = usages.filter(({ href }) => {
      if (href === null) return false; // a `pending` document names no route
      const path = href.split('?')[0]?.replace(/\/$/, '') || '/';
      return !exact.has(path) && !dynamic.some((prefix) => path.startsWith(`${prefix}/`));
    });

    expect(broken).toEqual([]);
  });

  it('go through the component, so the rule above cannot be sidestepped', () => {
    // Without this, the check above guards one component while a second
    // hand-written checkbox reintroduces the same defect beside it — which is
    // exactly how the first one got there.
    expect(handRolledConsent(SRC_DIR)).toEqual([]);
  });
});
