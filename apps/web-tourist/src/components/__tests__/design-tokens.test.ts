/**
 * No page sets its own colours.
 *
 * `packages/ui/src/globals.css` has held a complete design token set since
 * Phase 1 — light and dark, wired into Tailwind through
 * `@pumba/ui/tailwind-preset`, which both applications load. It was used by
 * almost nothing: **103 hard-coded `slate-*` and `gray-*` classes against 43
 * token classes**, across all eleven page and component files. Changing
 * `--primary` changed nothing anybody could see.
 *
 * That is the same defect as a port with no adapter, a renderer wired to no
 * view, and a fallback that could never fire — a mechanism that exists, is
 * configured correctly, and is connected to nothing. It is the seventh
 * instance this phase, and the only one that had survived three of them.
 *
 * Fixing it once is not enough. Nothing in a review reliably catches
 * `text-slate-600` in a diff — it looks exactly like working code, because it
 * is working code. It renders. It just renders a colour nobody chose, and it
 * makes the theme a lie: a site that cannot be restyled from its tokens does
 * not have tokens, it has a stylesheet nobody reads.
 *
 * So this is a build failure rather than a convention.
 *
 * **What is allowed.** `white` and `black` are permitted only through tokens,
 * so they are caught too. Arbitrary values (`bg-[#0a6a72]`) are caught,
 * because a hex code in a class is the same problem wearing brackets.
 * `transparent`, `current` and `inherit` are not colours and are fine.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';

import { describe, expect, it } from 'vitest';

const SRC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** Tailwind's built-in palettes. Every one of them is a colour we did not pick. */
const PALETTES = [
  'slate',
  'gray',
  'zinc',
  'neutral',
  'stone',
  'red',
  'orange',
  'amber',
  'yellow',
  'lime',
  'green',
  'emerald',
  'teal',
  'cyan',
  'sky',
  'blue',
  'indigo',
  'violet',
  'purple',
  'fuchsia',
  'pink',
  'rose',
].join('|');

/** `bg-`, `text-`, `border-`, `ring-`, `from-`, `divide-`, `fill-`… */
const PROPERTY = '(?:bg|text|border|ring|outline|divide|from|via|to|fill|stroke|shadow|decoration|placeholder|caret|accent)';

const RULES: { name: string; pattern: RegExp; fix: string }[] = [
  {
    name: 'a built-in Tailwind palette',
    pattern: new RegExp(`\\b${PROPERTY}-(?:${PALETTES})-\\d{2,3}\\b`, 'g'),
    fix: 'use a token: bg-background, text-muted-foreground, border-border, bg-primary, text-accent-ink…',
  },
  {
    name: 'a literal white or black',
    pattern: new RegExp(`\\b${PROPERTY}-(?:white|black)\\b`, 'g'),
    fix: 'use bg-card / text-card-foreground / bg-foreground — white is a token value, not a colour choice',
  },
  {
    name: 'an arbitrary colour value',
    pattern: new RegExp(`\\b${PROPERTY}-\\[(?:#|rgb|hsl)[^\\]]*\\]`, 'g'),
    fix: 'add a token to packages/ui/src/globals.css rather than inlining the value',
  },
  {
    name: 'a hard-coded transition duration',
    // The reduced-motion guard in `globals.css` flattens the duration
    // *tokens*. A component reaching past them for `duration-300` opts itself
    // out of that silently, which is the failure mode that rule exists to
    // prevent.
    pattern: /\bduration-(?!fast\b|base\b|slow\b)\[?\d+\]?\b/g,
    fix: 'use duration-fast / duration-base / duration-slow, which prefers-reduced-motion can flatten',
  },
];

/**
 * Source with comments removed.
 *
 * A rule that scans text cannot tell a class name from a mention of one, and
 * documentation that explains *why* a class is forbidden necessarily contains
 * it. That has now tripped a filesystem-scanning guard twice — a literal
 * `href` in a footer comment, and a literal duration in the docstring
 * explaining why literal durations are banned. Both were guards working
 * exactly as written, on text that was never code.
 *
 * Block comments only. `//` also begins the `https://` in every URL, and
 * stripping from there would eat the rest of the line.
 */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '');
}


/** Every `.tsx` under `src/`, except this file and the tests beside it. */
function sources(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === '__tests__' || entry.name === 'node_modules') continue;
      sources(full, found);
    } else if (entry.name.endsWith('.tsx')) {
      found.push(full);
    }
  }
  return found;
}

describe('the tourist site is styled from its tokens', () => {
  const files = sources(SRC_DIR);

  it('finds source files to check, so a passing run means something', () => {
    // Guards the guard. A walk that silently returned nothing would report
    // green for ever, which is precisely the shape of failure this file is
    // about.
    expect(files.length).toBeGreaterThan(10);
  });

  for (const rule of RULES) {
    it(`uses no ${rule.name}`, () => {
      const offences: string[] = [];

      for (const file of files) {
        const matches = code(readFileSync(file, 'utf8')).match(rule.pattern);
        if (matches) {
          offences.push(`${relative(SRC_DIR, file)}: ${[...new Set(matches)].join(', ')}`);
        }
      }

      expect(offences, `\n${offences.join('\n')}\n\n→ ${rule.fix}\n`).toEqual([]);
    });
  }
});
