'use client';

import { LocalTime, Money, cn } from '@pumba/ui';

import { FindingList } from '@/components/trip/findings';
import { byDay, findingsFor, type Finding, type ItineraryItem } from '@/lib/trips';

/**
 * The per-day timeline — SRS §24.14, §24.19.
 *
 * Two rules from the specification are structural here rather than left to
 * whoever styles this next.
 *
 * **An estimated leg says so, on the leg.** §12.6 requires the UI to render an
 * explicit "approximate" label for a haversine estimate, and the API carries
 * `is_approximate` on every item precisely so this component cannot forget.
 * Without it a modelled duration and a routed one look identical, and a tourist
 * plans a connection around a number nobody measured. Until Appendix D-2 is
 * decided *every* leg is estimated, so the label is on all of them — which is
 * honest, and is also the most visible argument for choosing a provider.
 *
 * **A locked item is not editable and shows why.** §10.3: an item covered by a
 * confirmed booking is never rewritten, and §24.14 asks for a padlock and no
 * drag handle. Rendering it identically to an editable row would invite a
 * gesture the server answers with a 409.
 *
 * Times are rendered by `LocalTime`, which formats in the destination's zone.
 * The server already resolved every day boundary there (a trip starting today
 * in Zanzibar is still yesterday in UTC for three hours), and a client that
 * reformatted in the browser's zone would undo that at the last step.
 */

const ICON: Record<string, string> = {
  STAY: '🛏',
  ACTIVITY: '🛶',
  ATTRACTION: '📍',
  TRANSFER: '🚗',
  FREE_TIME: '☕',
};

function ItemRow({
  item,
  findings,
  timezone,
  onRemove,
}: {
  item: ItineraryItem;
  findings: readonly Finding[];
  timezone: string;
  onRemove?: ((item: ItineraryItem) => void) | undefined;
}) {
  const own = findingsFor(item, findings);
  const isTransfer = item.item_type === 'TRANSFER';

  return (
    <li
      className={cn(
        'rounded-lg border p-3 sm:p-4',
        isTransfer ? 'border-dashed border-border bg-muted/40' : 'border-border bg-background',
      )}
    >
      <div className="flex items-start gap-3">
        <span aria-hidden className="text-lg leading-none">
          {ICON[item.item_type] ?? '•'}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p className="font-medium">{item.title}</p>
            {item.is_locked ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                <span aria-hidden>🔒</span>
                Booked — cannot be changed
              </span>
            ) : null}
          </div>

          <p className="mt-1 text-sm text-muted-foreground">
            <LocalTime value={item.starts_at} timeZone={timezone} display="time" />
            {' – '}
            <LocalTime value={item.ends_at} timeZone={timezone} display="time" />
          </p>

          {isTransfer && item.travel_seconds ? (
            <p className="mt-1 text-sm text-muted-foreground">
              {Math.round(item.travel_seconds / 60)} minutes
              {item.distance_m ? `, about ${Math.round(item.distance_m / 1000)} km` : null}
              {item.is_approximate ? (
                // §12.6's "explicit label". Not a tooltip: a caveat somebody
                // has to hover to discover is a caveat most people never see.
                <span className="ml-2 rounded-full bg-warning/15 px-2 py-0.5 text-xs text-warning-foreground">
                  approximate
                </span>
              ) : null}
            </p>
          ) : null}

          {item.line_total && item.currency ? (
            <p className="mt-1 text-sm font-medium">
              <Money value={{ amount: item.line_total, currency: item.currency }} />
            </p>
          ) : null}

          <FindingList findings={own} className="mt-3" />
        </div>

        {onRemove && !item.is_locked && !isTransfer ? (
          <button
            type="button"
            onClick={() => onRemove(item)}
            className="shrink-0 rounded-md px-2 py-1 text-sm text-muted-foreground transition-colors duration-fast ease-out hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Remove<span className="sr-only"> {item.title}</span>
          </button>
        ) : null}
      </div>
    </li>
  );
}

export function DayTimeline({
  items,
  findings,
  timezone,
  onRemove,
}: {
  items: readonly ItineraryItem[];
  findings: readonly Finding[];
  timezone: string;
  onRemove?: ((item: ItineraryItem) => void) | undefined;
}) {
  const days = byDay(items);

  if (days.size === 0) {
    return (
      // §24.14's "guided three-step prompt". An empty state that only says
      // "nothing here" makes the reader guess what to do next.
      <ol className="space-y-2 rounded-lg border border-dashed border-border p-6 text-sm text-muted-foreground">
        <li>1. Check your dates and party above.</li>
        <li>2. Add where you are staying.</li>
        <li>3. Add the things you want to do — then plan the days.</li>
      </ol>
    );
  }

  return (
    <div className="space-y-8">
      {[...days.entries()].map(([day, dayItems]) => (
        <section key={day} aria-labelledby={`day-${day}`}>
          <h3 id={`day-${day}`} className="font-display text-lg font-semibold tracking-tight">
            Day {day}
          </h3>
          <ul className="mt-3 space-y-3">
            {dayItems.map((item) => (
              <ItemRow
                key={item.public_id}
                item={item}
                findings={findings}
                timezone={timezone}
                onRemove={onRemove}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
