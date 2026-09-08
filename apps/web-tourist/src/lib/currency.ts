/**
 * The currency prices are shown in — SRS §9.1, §24.1, ADR 0024.
 *
 * Every price the API stores is in the destination's currency, which for
 * Zanzibar is Tanzanian shillings. §9.1 lets a client send `X-Currency` and
 * §24.1 gives the tourist a chooser; this holds the choice and puts it on
 * every request.
 *
 * **It is never the currency anything is charged in.** The server converts for
 * display only, sends both figures, and the charged one is what `trip.currency`
 * says — locked at first pricing (BR-016) and taken from the destination
 * (§4.2). Changing this setting changes what a page reads, never what a card
 * is debited.
 *
 * **The header is attached in `apiFetch` rather than at each call site.** A
 * forgotten call site would render in shillings with nothing on the page to
 * explain why, which is indistinguishable from the feature not existing — the
 * same reasoning `apps/common/presentment.py` gives for using a context
 * variable on the server rather than serializer context.
 */

const STORAGE_KEY = 'pumba.currency';

/**
 * `currency.enabled` as the API ships it today.
 *
 * A local copy of a `system_setting`, and it is allowed to be stale in exactly
 * one direction: the switcher may offer fewer currencies than the platform
 * supports, never more. `GET /config` publishes `enabled_currencies`, and
 * `loadEnabledCurrencies` replaces this the moment it answers — until then a
 * first paint has something to draw rather than an empty menu.
 */
export const FALLBACK_CURRENCIES = ['USD', 'EUR', 'GBP', 'TZS'] as const;

let current: string | null | undefined;

/** Fires when the choice changes, so every mounted price re-fetches. */
export const CURRENCY_CHANGED = 'pumba:currency-changed';

function isBrowser(): boolean {
  return typeof window !== 'undefined';
}

/**
 * The chosen currency, or `null` for "show me what it is priced in".
 *
 * `null` is a real answer and not a missing one: a tourist who has made no
 * choice sees the listing currency, which is the honest default and the one
 * that needs no conversion or rate to render.
 */
export function getCurrency(): string | null {
  if (current !== undefined) return current;
  if (!isBrowser()) return null;
  try {
    current = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private browsing, or storage disabled. A currency preference is not
    // worth failing a page over.
    current = null;
  }
  return current;
}

export function setCurrency(code: string | null): void {
  current = code;
  if (!isBrowser()) return;
  try {
    if (code === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, code);
  } catch {
    // Ignored for the same reason as above; the choice still applies to this
    // session, it simply will not survive a reload.
  }
  window.dispatchEvent(new CustomEvent(CURRENCY_CHANGED, { detail: code }));
}

/**
 * The `X-Currency` header, or nothing.
 *
 * Nothing rather than an empty value: the server treats a blank header as
 * absent anyway, and sending one would put a needless entry in every `Vary`
 * cache key.
 */
export function currencyHeaders(): Record<string, string> {
  const code = getCurrency();
  return code ? { 'X-Currency': code } : {};
}
