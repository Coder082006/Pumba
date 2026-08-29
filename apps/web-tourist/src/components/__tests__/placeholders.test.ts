/**
 * Invented contact details stay in one file.
 *
 * The footer carries a support address, a phone number and a company name
 * that are not real. I argued against inventing them — a fake support address
 * is read by a stranded tourist at the worst moment of their trip — and was
 * overruled, which is the product owner's call to make.
 *
 * What is not their call is whether the placeholders are findable later. The
 * failure mode of a placeholder is not that it is wrong today; it is that it
 * survives, because by the time anybody looks it has been copied into four
 * components and reads like real data. So:
 *
 * 1. The values live only in `lib/placeholders.ts`. This fails the build if
 *    one appears anywhere else under `src/`.
 * 2. They render with a visible marker on the page, so the person reviewing
 *    the site is told and not only the person reading the source.
 *
 * Clearing them is then a five-minute job in one file rather than a search.
 *
 * The rule survives the placeholders themselves: once real values land, this
 * still stops somebody hard-coding the support address into a third component
 * where a change of number would miss it.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';

import { describe, expect, it } from 'vitest';

import { CONTACT, IS_PLACEHOLDER } from '@/lib/placeholders';

const SRC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const HOME = join(SRC_DIR, 'lib', 'placeholders.ts');

function sources(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules') continue;
      sources(full, found);
    } else if (/\.(ts|tsx)$/.test(entry.name) && full !== HOME) {
      found.push(full);
    }
  }
  return found;
}

describe('placeholder contact details', () => {
  const files = sources(SRC_DIR).filter((f) => !f.endsWith('placeholders.test.ts'));

  it('finds source files to check, so a passing run means something', () => {
    expect(files.length).toBeGreaterThan(10);
  });

  for (const [field, value] of Object.entries(CONTACT)) {
    it(`keeps ${field} out of every file but lib/placeholders.ts`, () => {
      const offenders = files
        .filter((file) => readFileSync(file, 'utf8').includes(value))
        .map((file) => relative(SRC_DIR, file));

      expect(
        offenders,
        `\n"${value}" is hard-coded in:\n${offenders.join('\n')}\n\n` +
          '→ import it from @/lib/placeholders instead, so there is one place to change.\n',
      ).toEqual([]);
    });
  }

  it('uses a non-routable address, so no real inbox can receive by accident', () => {
    // RFC 2606 reserves `example.com` precisely so it can never be
    // registered. A plausible-looking address might belong to somebody.
    expect(CONTACT.email.endsWith('@example.com')).toBe(true);
  });

  it('is still flagged as a launch blocker', () => {
    // This assertion is meant to be deleted, not maintained. When the real
    // details land, `IS_PLACEHOLDER` goes false, this fails, and whoever is
    // holding it removes this test and the marker from the footer together —
    // which is the point: the flag cannot be flipped and forgotten.
    expect(IS_PLACEHOLDER).toBe(true);
  });
});
