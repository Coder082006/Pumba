/**
 * The shared components are styled from tokens too.
 *
 * `apps/web-tourist` has had this check since the palette was adopted. This
 * package did not — and that gap was not theoretical. `map.tsx` and
 * `gallery.tsx` kept four raw `slate-*` classes through the whole adoption
 * pass, and one of them, the map's attribution `figcaption` in
 * `text-slate-500` on a `bg-slate-100` figure, was **an actual WCAG contrast
 * failure** that Lighthouse scored against the destination page. It is part
 * of why CI's accessibility gate has been red since the day it was added.
 *
 * A guard that covers one package and not the one holding the components
 * every page renders is worse than no guard, because it reads like coverage.
 * So the rule now lives on both sides of the workspace boundary.
 *
 * Kept as a copy rather than shared from `web-tourist`: this package must not
 * take a dev dependency on an app that consumes it, and the rule is fifteen
 * lines. Two short copies that both run beat one shared helper that inverts
 * the dependency graph.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';

import { describe, expect, it } from 'vitest';

const SRC_DIR = dirname(fileURLToPath(import.meta.url));

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

const PROPERTY =
  '(?:bg|text|border|ring|outline|divide|from|via|to|fill|stroke|shadow|decoration|placeholder|caret|accent)';

const RULES: { name: string; pattern: RegExp; fix: string }[] = [
  {
    name: 'a built-in Tailwind palette',
    pattern: new RegExp(`\\b${PROPERTY}-(?:${PALETTES})-\\d{2,3}\\b`, 'g'),
    fix: 'use a token: bg-muted, text-muted-foreground, border-border, bg-primary…',
  },
  {
    name: 'a literal white or black',
    pattern: new RegExp(`\\b${PROPERTY}-(?:white|black)\\b`, 'g'),
    fix: 'use bg-card / text-card-foreground / bg-foreground',
  },
  {
    name: 'an arbitrary colour value',
    pattern: new RegExp(`\\b${PROPERTY}-\\[(?:#|rgb|hsl)[^\\]]*\\]`, 'g'),
    fix: 'add a token to src/globals.css rather than inlining the value',
  },
  {
    name: 'a hard-coded transition duration',
    pattern: /\bduration-(?!fast\b|base\b|slow\b)\[?\d+\]?\b/g,
    fix: 'use duration-fast / duration-base / duration-slow so reduced-motion can flatten it',
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


function sources(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules') continue;
      sources(full, found);
    } else if (entry.name.endsWith('.tsx') && !entry.name.endsWith('.test.tsx')) {
      found.push(full);
    }
  }
  return found;
}

describe('packages/ui is styled from its tokens', () => {
  const files = sources(SRC_DIR);

  it('finds components to check, so a passing run means something', () => {
    expect(files.length).toBeGreaterThan(3);
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
