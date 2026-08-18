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

const TONES: Record<StatusTone, string> = {
  neutral: 'bg-muted text-muted-foreground',
  pending: 'bg-secondary text-secondary-foreground',
  success: 'bg-accent/15 text-accent',
  warning: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
  danger: 'bg-destructive/10 text-destructive',
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
