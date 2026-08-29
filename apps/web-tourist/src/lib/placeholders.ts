/**
 * Contact details that are not real yet. **Launch blocker.**
 *
 * ---------------------------------------------------------------------------
 * Why this file exists at all
 * ---------------------------------------------------------------------------
 *
 * The footer needs a Contact column and nobody has supplied a support address,
 * a phone number or a registered company name. I argued against inventing
 * them: a fake support address on a tourism site is read by a stranded tourist
 * at the worst moment of their trip, and it is the same class of fabrication
 * as a distance chip computed from a hash. That objection was heard and
 * overruled, which is a decision the product owner gets to make.
 *
 * So the placeholders exist — and they are made **loud** rather than quiet,
 * because the failure mode of a placeholder is not that it is wrong today. It
 * is that it survives. A plausible-looking `info@pumba.co.tz` scattered across
 * four components is unfindable; a marked one in a single module is a
 * five-minute job to clear.
 *
 * ---------------------------------------------------------------------------
 * What holds it in place
 * ---------------------------------------------------------------------------
 *
 * 1. Every value lives here and nowhere else.
 *    `__tests__/placeholders.test.ts` fails the build if any of these strings
 *    appears in any other file under `src/`, so the set cannot be spread by
 *    copy-paste.
 * 2. Every value renders with a visible marker (see `PlaceholderNote`), so a
 *    reader of the page is told, not just a reader of the source.
 * 3. `IS_PLACEHOLDER` is exported so a future release check can refuse to
 *    build for production while it is true.
 *
 * Recorded in the phase report under item 16, beside the terms of use, the
 * privacy notice and the PDPC registration — all of which are the product
 * owner's to resolve.
 *
 * ---------------------------------------------------------------------------
 * Clearing it
 * ---------------------------------------------------------------------------
 *
 * Replace the values, set `IS_PLACEHOLDER` to `false`, delete
 * `PlaceholderNote` from the footer, and remove the item from the phase
 * report. The test then guards the real values against being edited into
 * scattered literals, which is worth keeping.
 */

/**
 * `true` while any value below is invented.
 *
 * Deliberately not derived from the values — a derived flag would quietly
 * flip when somebody filled in one field of four and left the rest.
 */
export const IS_PLACEHOLDER = true;

/**
 * Non-routable on purpose.
 *
 * `example.com` is reserved by RFC 2606 precisely so it can never be
 * registered, so this address cannot reach a real inbox by accident, and the
 * number is not a dialable pattern in any plan. A plausible address would be
 * worse: it might belong to somebody.
 */
export const CONTACT = {
  email: 'support@example.com',
  phone: '+000 000 000 000',
  /**
   * The registered legal entity for the copyright line.
   *
   * Not the same string as the wordmark. "Pumba" is the product name and
   * appears legitimately in the header and the hero; the entity that holds
   * the copyright is a company that does not exist yet. Keeping them
   * distinct is what lets the guard test below mean something — a
   * placeholder equal to the brand name would match every page and have to
   * be exempted, which is how a guard stops guarding.
   */
  company: 'Example Tours Limited',
} as const;

export type ContactField = keyof typeof CONTACT;
