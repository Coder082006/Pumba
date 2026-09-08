'use client';

import { useEffect, useState } from 'react';

import { getPublicConfig } from '@/lib/config';
import { FALLBACK_CURRENCIES, getCurrency, setCurrency } from '@/lib/currency';

/**
 * §24.1's currency chooser.
 *
 * SRS §24.1 lists "presentment currency" among the three things the client
 * resolves before showing anything, and §9.1 carries the answer as
 * `X-Currency`. This is where a tourist arriving from Frankfurt stops reading
 * `TZS 45,000` and starts reading `EUR 16.56`.
 *
 * **The options come from the server, not from here.** `GET /config` publishes
 * `enabled_currencies` from the `currency.enabled` setting, so adding CHF is an
 * administrator's edit rather than a release of this file (hard rule 5). The
 * local list is a first-paint fallback and is allowed to be short, never long:
 * offering a currency the platform cannot convert would produce a page of
 * unconverted prices with no explanation.
 *
 * **"Local prices" is a real option and the default.** A tourist who has made
 * no choice sees what things are priced in, which needs no rate and cannot be
 * stale. Removing it would force everybody through a conversion they may not
 * want.
 *
 * A client island. The header is server-rendered and this reads
 * `localStorage`, which the server has no access to — the same split
 * `AccountMenu` makes for the same reason.
 */
export function CurrencySwitcher() {
  const [choice, setChoice] = useState<string | null>(null);
  const [options, setOptions] = useState<readonly string[]>(FALLBACK_CURRENCIES);

  useEffect(() => {
    setChoice(getCurrency());
    getPublicConfig()
      .then((config) => {
        if (config.enabled_currencies?.length) setOptions(config.enabled_currencies);
      })
      .catch(() => {
        // The fallback list is already on screen. A config fetch that fails
        // must not empty a menu the tourist is looking at.
      });
  }, []);

  function choose(value: string) {
    const next = value === '' ? null : value;
    setChoice(next);
    setCurrency(next);
    // Every price on the page came from a response that carried the old
    // header, so the honest way to apply the change is to ask again. A reload
    // rather than a cache bust because prices appear on server-rendered pages
    // too, and a partial refresh would leave two currencies on one screen.
    window.location.reload();
  }

  return (
    <label className="flex items-center gap-1 text-sm">
      <span className="sr-only">Show prices in</span>
      <select
        value={choice ?? ''}
        onChange={(event) => choose(event.target.value)}
        aria-label="Show prices in"
        className="rounded-md border border-border bg-transparent px-2 py-1 text-sm font-medium text-foreground/80 transition-colors duration-fast hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <option value="">Local prices</option>
        {options.map((code) => (
          <option key={code} value={code}>
            {code}
          </option>
        ))}
      </select>
    </label>
  );
}
