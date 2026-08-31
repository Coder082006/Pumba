'use client';

import { cn } from '@pumba/ui';

import type { Finding } from '@/lib/trips';

/**
 * Validation findings — SRS §10.6, §24.14.
 *
 * §10.6 gives every finding `item_ids` *"so the client can render an inline fix
 * affordance"*, and that clause is the whole design of this file. A banner at
 * the top of a page saying "3 problems with this trip" is the version people
 * learn to dismiss: it names no row, offers no action, and reappears
 * identically after a change that fixed two of them. So a finding is rendered
 * against the item it names, and only the ones that name *nothing* — VR-16 is
 * about nights that have no stay — appear as a banner, because there is
 * genuinely nowhere else for them to go.
 *
 * **Severity is two-valued and the colours are not decorative.** §10.6: an
 * ERROR blocks quoting and a WARNING advises. Rendering both the same would
 * make a tourist read the advisory ones as blockers and the blockers as noise.
 */

const TONE: Record<string, string> = {
  ERROR: 'border-destructive/40 bg-destructive/10 text-destructive-ink',
  WARNING: 'border-warning-border bg-warning/10 text-warning-foreground',
};

export function FindingChip({ finding }: { finding: Finding }) {
  return (
    <p
      className={cn(
        'flex gap-2 rounded-md border px-3 py-2 text-sm leading-relaxed',
        TONE[finding.severity] ?? TONE.WARNING,
      )}
    >
      {/* The code is shown, quietly. A tourist ignores it; somebody reading a
          support email quotes it, and §10.6 keys the client's copy on it. */}
      <span className="shrink-0 font-mono text-xs opacity-70">{finding.code}</span>
      <span>{finding.message}</span>
    </p>
  );
}

export function FindingList({
  findings,
  className,
}: {
  findings: readonly Finding[];
  className?: string;
}) {
  if (findings.length === 0) return null;
  return (
    <div className={cn('space-y-2', className)}>
      {findings.map((finding, index) => (
        <FindingChip key={`${finding.code}-${index}`} finding={finding} />
      ))}
    </div>
  );
}

/**
 * The one-line answer to "can this be quoted" — §10.6.
 *
 * Derived from the itinerary's own `has_errors`, which the server computes, so
 * the client never decides for itself what blocks. A UI that counted ERROR
 * findings locally would be a second implementation of the quote gate.
 */
export function ValidationBanner({
  hasErrors,
  generated,
}: {
  hasErrors: boolean;
  generated: boolean;
}) {
  if (!generated) {
    return (
      <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
        This trip has not been planned yet. Add what you want to do, then choose
        <span className="font-medium"> Plan the days</span>.
      </p>
    );
  }
  return hasErrors ? (
    <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm font-medium text-destructive-ink">
      Some things need fixing before this trip can be priced.
    </p>
  ) : (
    <p className="rounded-md border border-border bg-accent/10 px-3 py-2 text-sm text-foreground">
      This plan looks workable. Anything flagged below is advisory.
    </p>
  );
}
