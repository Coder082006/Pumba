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
 */
export interface MoneyValue {
  amount: string;
  currency: string;
}

export interface MoneyProps extends React.HTMLAttributes<HTMLSpanElement> {
  value: MoneyValue;
  locale?: string;
}

export function Money({ value, locale, className, ...props }: MoneyProps) {
  let formatted: string;
  try {
    formatted = new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: value.currency,
    }).format(
      // `Intl` accepts a string and formats it as an exact decimal, avoiding
      // the float round-trip entirely.
      value.amount as unknown as number,
    );
  } catch {
    formatted = `${value.currency} ${value.amount}`;
  }

  return (
    <span className={cn('tabular-nums', className)} {...props}>
      {formatted}
    </span>
  );
}
