import * as React from 'react';

import { cn } from '../lib/cn';

/**
 * Renders one of the platform's status values (SRS Appendix A).
 *
 * Colour alone never carries the meaning — the label is always present — so
 * the component remains legible to colour-blind users and in monochrome
 * print, which matters because tourists print itineraries (SRS §29.8).
 */
export type StatusTone = 'neutral' | 'pending' | 'success' | 'warning' | 'danger';

/**
 * Every tone is a token pair, so a theme change reaches all five.
 *
 * `warning` was the exception until the `--warning` tokens existed: raw
 * `amber-100/900` plus a hand-written `dark:` variant, which is two colours
 * this design system did not know it had. The token carries its own dark
 * value, so the variant disappears rather than being translated.
 *
 * `success` and `danger` set text in the *ink* variants. `accent` and
 * `destructive` are surface colours — bright amber on a pale tint is 2.9:1,
 * and a badge nobody can read is worse than no badge.
 */
const TONES: Record<StatusTone, string> = {
  neutral: 'bg-muted text-muted-foreground',
  pending: 'bg-secondary text-secondary-foreground',
  success: 'bg-accent/15 text-accent-ink',
  warning: 'bg-warning text-warning-foreground',
  danger: 'bg-destructive/10 text-destructive-ink',
};

export interface StatusBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: StatusTone;
  label: string;
}

export function StatusBadge({ tone = 'neutral', label, className, ...props }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
        TONES[tone],
        className,
      )}
      {...props}
    >
      {label}
    </span>
  );
}
