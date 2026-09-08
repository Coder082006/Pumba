import * as React from 'react';

import { cn } from '../lib/cn';

/**
 * Renders a wire-format money object (SRS §9.1).
 *
 * Takes the decimal string straight from the API and hands it to
 * `Intl.NumberFormat` without ever converting to a JS number, because a
 * number would be an IEEE 754 double and could not represent every decimal
 * amount exactly. If the runtime cannot format the currency, the raw string
 * is shown rather than a wrong figure.
 *
 * **`display` is what the tourist asked to read; `value` is what they pay.**
 * SRS §24.1 lets them choose a currency and §9.1 carries it as `X-Currency`;
 * the server converts and sends both, because ADR 0024 keeps the charged
 * figure and the converted one apart all the way to the screen. So this
 * component leads with the converted number — that is the one somebody
 * arriving from Frankfurt can judge — and prints the charged one underneath,
 * labelled, so nobody is surprised at the card machine.
 *
 * When nothing was converted (`display` absent or null) it renders exactly as
 * it did before: one figure, no qualifier, no second line.
 */
export interface MoneyValue {
  amount: string;
  currency: string;
}

/** A converted figure and the evidence of where it came from (SRS §20.6). */
export interface DisplayMoneyValue extends MoneyValue {
  rate: string;
  as_of: string;
  source: string;
}

export interface MoneyProps extends React.HTMLAttributes<HTMLSpanElement> {
  value: MoneyValue;
  /** The same amount in the tourist's chosen currency, if one was asked for. */
  display?: DisplayMoneyValue | null | undefined;
  locale?: string;
  /**
   * Suppresses the charged figure. For dense places — a day timeline, a list
   * row — where the second line would crowd everything else out. The charged
   * currency is still named on the screen's total, which is where a tourist
   * looks before paying.
   */
  compact?: boolean;
}

function format(value: MoneyValue, locale?: string): string {
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: value.currency,
    }).format(
      // `Intl` accepts a string and formats it as an exact decimal, avoiding
      // the float round-trip entirely.
      value.amount as unknown as number,
    );
  } catch {
    return `${value.currency} ${value.amount}`;
  }
}

export function Money({ value, display, locale, compact, className, ...props }: MoneyProps) {
  const charged = format(value, locale);

  if (!display) {
    return (
      <span className={cn('tabular-nums', className)} {...props}>
        {charged}
      </span>
    );
  }

  const converted = format(display, locale);

  if (compact) {
    return (
      <span className={cn('tabular-nums', className)} title={`Charged as ${charged}`} {...props}>
        {converted}
        <span className="ml-1 text-xs font-normal text-muted-foreground">approx.</span>
      </span>
    );
  }

  return (
    <span className={cn('inline-flex flex-col items-end tabular-nums', className)} {...props}>
      <span>
        {converted}
        <span className="ml-1 text-xs font-normal text-muted-foreground">approx.</span>
      </span>
      <span className="text-xs font-normal text-muted-foreground">
        charged as {charged}
      </span>
    </span>
  );
}
