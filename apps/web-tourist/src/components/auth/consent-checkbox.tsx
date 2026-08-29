'use client';

import Link from 'next/link';

/**
 * A consent control and the document it consents to, bound together.
 *
 * Registration shipped with a required checkbox reading "I accept the terms of
 * use", whose link was a 404. Somebody was agreeing to a document they could
 * not open, and the form would not submit until they did. Removing the dead
 * link fixed the 404 and left the worse half: a consent control with no
 * referent at all.
 *
 * Both failures have the same shape — the control and its document drifted
 * apart, and nothing held them together. So this component makes the document
 * a **required, typed** prop with no default and no optional variant. There is
 * no way to render a consent checkbox here without stating, in the type
 * system, which document it is for:
 *
 *   - `{ href, name }` — the document exists and is at that route.
 *     `__tests__/navigation.test.ts` resolves the href against the App Router
 *     directory and fails the build if it does not exist, so the 404 that
 *     started this cannot recur.
 *   - `{ pending, name }` — the document is not published yet. Renders as a
 *     stated gap rather than a bare claim, because a checkbox asserting
 *     agreement to an unnamed, unreachable thing is not consent in any sense
 *     worth having, and hiding that makes it look like consent anyway.
 *
 * The second variant is deliberately uncomfortable to look at. It is meant to
 * be: it is the visible form of a launch blocker, and it disappears the moment
 * the document is published and the call site changes one word.
 */

export type ConsentDocument =
  | { name: string; href: string; pending?: never }
  | { name: string; pending: true; href?: never };

export interface ConsentCheckboxProps {
  id: string;
  document: ConsentDocument;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

export function ConsentCheckbox({
  id,
  document,
  checked,
  onCheckedChange,
}: ConsentCheckboxProps) {
  const hintId = `${id}-hint`;

  return (
    <div className="space-y-1">
      <label className="flex items-start gap-2 text-sm">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(event) => onCheckedChange(event.target.checked)}
          aria-describedby={hintId}
          className="mt-1"
        />
        <span id={hintId}>
          {document.pending ? (
            <>I accept the {document.name}.</>
          ) : (
            <>
              I accept the{' '}
              <Link href={document.href} className="underline">
                {document.name}
              </Link>
              .
            </>
          )}
        </span>
      </label>

      {document.pending ? (
        // Named plainly, next to the control it undermines. The alternative —
        // saying nothing — is what "I accept the terms of use." with no link
        // already does, and that reads as though a document exists.
        <p role="note" className="ml-6 text-xs text-warning-foreground">
          The {document.name} {document.name.endsWith('s') ? 'are' : 'is'} not published yet.
          Until {document.name.endsWith('s') ? 'they are' : 'it is'}, there is nothing here for
          you to read before agreeing.
        </p>
      ) : null}
    </div>
  );
}
