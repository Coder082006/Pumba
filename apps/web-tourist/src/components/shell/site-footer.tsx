import Link from 'next/link';
import { Suspense } from 'react';

import { CONTACT, IS_PLACEHOLDER } from '@/lib/placeholders';
import { listDestinations, listMarkets } from '@/lib/catalogue';

/**
 * The persistent footer — modelled on nordicvisitor.com's, which is five
 * columns: Destinations, About, Support, Contact and Connect.
 *
 * **The columns that exist are the ones with something behind them.** That is
 * the same rule the rest of this phase follows, and here it is enforced
 * mechanically rather than by care: `__tests__/navigation.test.ts` fails the
 * build on any link that resolves to no route. A footer full of plausible
 * links to 404s is exactly the defect that test was written for — six of them
 * shipped, one broken since Phase 2, and nothing noticed because a header
 * renders perfectly well while pointing at nothing.
 *
 * So the following are **omitted, not stubbed**: About us, Our staff, Why book
 * with us, Reviews (`review` is a Phase 1 skeleton), Sustainability, Booking
 * terms, Travel updates, offices, licences, social accounts and the blog.
 * There is no page behind any of them and none can honestly be written yet.
 *
 * Terms of use, the privacy notice and the cookie policy stay absent for a
 * different reason: they are legal documents with a named controller and a
 * retention schedule, they are the product owner's to produce, and a stub
 * carrying one of those titles is worse than an absent link rather than
 * better. Phase report, item 16.
 *
 * **The market column is the interesting one.** It is read from `/markets`, so
 * opening a market fills this footer with no deployment — §4.1's promise
 * showing up somewhere people actually look. An announced market renders as
 * plain text and not a link, because its catalogue 404s by design; linking it
 * would be the precise failure the navigation test exists to catch.
 *
 * Each column fetches independently and degrades to nothing, as the sections
 * on Explore do. A footer is not worth a 500 on every page.
 *
 * **Both fetching columns sit behind `<Suspense>` with a fallback of the same
 * height, and that is a layout requirement rather than a nicety.** Without it
 * the whole footer is an async boundary: the page paints, the two fetches
 * resolve, the footer appears and everything above it settles — measured at
 * **CLS 0.077 against a 0.1 budget**, from a single element, and it was the
 * largest layout shift on the site. A reserved box costs nothing and the
 * footer now renders at its final height immediately.
 */

/** Same height as a filled column, so the footer never changes size. */
function ColumnSkeleton({ title }: { title: string }) {
  return (
    <div aria-hidden>
      <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        {title}
      </h2>
      <ul className="mt-4 space-y-2.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <li key={i} className="h-5 w-28 rounded bg-border/60" />
        ))}
      </ul>
    </div>
  );
}

/** Marks a value that is not real yet, on the page rather than in a comment. */
function PlaceholderNote() {
  return (
    <span className="ml-1.5 rounded border border-warning-border bg-warning px-1 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-warning-foreground">
      placeholder
    </span>
  );
}

function Column({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        {title}
      </h2>
      <ul className="mt-4 space-y-2.5 text-sm">{children}</ul>
    </div>
  );
}

function FooterLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <li>
      <Link
        href={href}
        className="text-foreground/80 transition-colors duration-fast ease-out hover:text-primary"
      >
        {children}
      </Link>
    </li>
  );
}

async function MarketColumn() {
  let markets;
  try {
    markets = await listMarkets();
  } catch {
    return null;
  }
  if (markets.length === 0) return null;

  return (
    <Column title="Where we go">
      {markets.map((market) =>
        market.is_open ? (
          <FooterLink key={market.slug} href="/explore">
            {market.name}
          </FooterLink>
        ) : (
          // Not a link. Its catalogue is deliberately unreachable until it
          // opens, and `navigation.test.ts` would fail the build for one.
          <li key={market.slug} className="text-muted-foreground">
            {market.name}
            <span className="ml-1.5 text-xs">· opening soon</span>
          </li>
        ),
      )}
    </Column>
  );
}

async function DestinationColumn() {
  let items;
  try {
    ({ items } = await listDestinations({ limit: 8 }));
  } catch {
    return null;
  }
  if (items.length === 0) return null;

  return (
    <Column title="Destinations">
      {items.map((destination) => (
        <FooterLink key={destination.slug} href={`/destinations/${destination.slug}`}>
          {destination.name}
        </FooterLink>
      ))}
    </Column>
  );
}

// Deliberately **not** `async`. It has no `await` of its own — both fetches
// live in the Suspense-wrapped children — and an `async` component returns a
// promise whether or not it awaits anything, which makes React suspend the
// entire footer and streams it in after paint. That is what the inner
// boundaries were added to prevent, and leaving the keyword on defeats them
// silently: the markup is identical and only the shift betrays it.
export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-border bg-muted">
      <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
        <div className="grid grid-cols-2 gap-10 md:grid-cols-4">
          <div className="col-span-2 md:col-span-1">
            <p className="font-display text-xl font-bold tracking-tight">Pumba</p>
            <p className="mt-3 max-w-xs text-sm leading-relaxed text-muted-foreground">
              Plan the whole journey before you travel — where you stay, what you do, and how you
              get between them.
            </p>
          </div>

          <Suspense fallback={<ColumnSkeleton title="Where we go" />}>
            <MarketColumn />
          </Suspense>
          <Suspense fallback={<ColumnSkeleton title="Destinations" />}>
            <DestinationColumn />
          </Suspense>

          <Column title="Plan">
            <FooterLink href="/explore">Explore</FooterLink>
            <FooterLink href="/stays">Where to stay</FooterLink>
            <FooterLink href="/login">Sign in</FooterLink>
            <li>
              {/* A plain anchor, not a `<Link>`: the sitemap is a generated
                  document served by `app/sitemap.ts`, not an App Router page,
                  so it has no entry in the route table `navigation.test.ts`
                  builds from the filesystem. */}
              <a
                href="/sitemap.xml"
                className="text-foreground/80 transition-colors duration-fast ease-out hover:text-primary"
              >
                Sitemap
              </a>
            </li>
          </Column>
        </div>

        <div className="mt-12 border-t border-border pt-8">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
            Contact
          </h2>
          <ul className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
            <li>
              <a
                href={`mailto:${CONTACT.email}`}
                className="text-foreground/80 transition-colors duration-fast ease-out hover:text-primary"
              >
                {CONTACT.email}
              </a>
              {IS_PLACEHOLDER ? <PlaceholderNote /> : null}
            </li>
            <li>
              <a
                href={`tel:${CONTACT.phone.replace(/\s/g, '')}`}
                className="text-foreground/80 transition-colors duration-fast ease-out hover:text-primary"
              >
                {CONTACT.phone}
              </a>
              {IS_PLACEHOLDER ? <PlaceholderNote /> : null}
            </li>
          </ul>
        </div>

        <p className="mt-10 text-xs text-muted-foreground">
          © {new Date().getFullYear()} {CONTACT.company}. All rights reserved.
          {IS_PLACEHOLDER ? <PlaceholderNote /> : null}
        </p>
      </div>
    </footer>
  );
}
