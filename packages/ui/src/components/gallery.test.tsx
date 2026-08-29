/**
 * The gallery, and the credit that has to appear beside a licensed image.
 *
 * This is the half of the licence rule that lives in the browser, and it is
 * the half that can fail silently. PostgreSQL refuses to *store* a licensed
 * image with no attribution; nothing except this file checks that the
 * attribution then reaches a reader. A payload full of correct credits that
 * renders none of them is a licence breach with a completely green test suite
 * behind it — the exact shape of defect this phase has now found seven times.
 *
 * So these assert the rendered output, not the props.
 */

import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Gallery, ImageCredit, type GalleryImage } from './gallery';

const CC_BY = 'https://creativecommons.org/licenses/by/4.0/';
const SOURCE = 'https://commons.wikimedia.org/wiki/File:Stone_Town.jpg';

function image(overrides: Partial<GalleryImage> = {}): GalleryImage {
  return {
    file_key: 'stone-town.webp',
    alt_text: 'Carved doors on a coral-stone street.',
    width: 1600,
    height: 900,
    is_primary: true,
    sort_order: 0,
    attribution: '',
    license_code: '',
    license_url: '',
    source_url: '',
    ...overrides,
  };
}

const srcFor = (key: string) => `/media/${key}`;

/**
 * The rendered credit lines, as text.
 *
 * Queried this way rather than with `getByText` because an uncredited-source
 * image renders the name as a bare text node beside the separator and the
 * licence, so the caption's text is split across nodes. Reading
 * `textContent` asserts what a person actually sees.
 */
function captions(container: HTMLElement): string[] {
  return [...container.querySelectorAll('figcaption')].map((node) => node.textContent ?? '');
}

describe('the credit beside a licensed image', () => {
  it('names the photographer and the licence', () => {
    render(
      <Gallery
        images={[
          image({
            attribution: 'A. Photographer',
            license_code: 'CC BY 4.0',
            license_url: CC_BY,
            source_url: SOURCE,
          }),
        ]}
        srcFor={srcFor}
      />,
    );

    const caption = screen.getByText('A. Photographer').closest('figcaption');
    expect(caption).not.toBeNull();
    expect(within(caption!).getByText('CC BY 4.0')).toBeTruthy();
  });

  it('links the licence with rel="license", so the condition is identifiable', () => {
    // CC BY asks for the licence to be identified, not merely named. A bare
    // string saying "CC BY 4.0" is a claim; the deed URL is the thing.
    render(
      <Gallery
        images={[
          image({ attribution: 'A. Photographer', license_code: 'CC BY 4.0', license_url: CC_BY }),
        ]}
        srcFor={srcFor}
      />,
    );

    const link = screen.getByText('CC BY 4.0') as HTMLAnchorElement;
    expect(link.tagName).toBe('A');
    expect(link.getAttribute('href')).toBe(CC_BY);
    expect(link.getAttribute('rel')).toContain('license');
  });

  it('links the credit back to the source page so the claim can be checked', () => {
    render(
      <Gallery
        images={[
          image({
            attribution: 'A. Photographer',
            license_code: 'CC BY 4.0',
            license_url: CC_BY,
            source_url: SOURCE,
          }),
        ]}
        srcFor={srcFor}
      />,
    );

    expect((screen.getByText('A. Photographer') as HTMLAnchorElement).getAttribute('href')).toBe(
      SOURCE,
    );
  });

  it('renders no credit for own work', () => {
    // `license_code: ''` is a positive claim that the Platform owns the
    // picture, and a credit line under it would be noise on every page.
    const { container } = render(<Gallery images={[image()]} srcFor={srcFor} />);
    expect(container.querySelector('figcaption')).toBeNull();
  });

  it('still credits an image whose source page is unknown', () => {
    // The licence condition is attribution. A missing source URL degrades the
    // credit to plain text; it must not remove it.
    const { container } = render(
      <Gallery
        images={[image({ attribution: 'A. Photographer', license_code: 'PD', license_url: CC_BY })]}
        srcFor={srcFor}
      />,
    );
    expect(captions(container)).toEqual(['A. Photographer · PD']);
  });

  it('credits every image in the grid, not only the first', () => {
    // The first image gets the large cell and is the one anybody eyeballs.
    // A credit rendered only there would look right in review.
    const { container } = render(
      <Gallery
        images={[
          image({
            file_key: 'a.webp',
            attribution: 'First',
            license_code: 'CC0',
            license_url: CC_BY,
          }),
          image({
            file_key: 'b.webp',
            is_primary: false,
            sort_order: 1,
            attribution: 'Second',
            license_code: 'CC0',
            license_url: CC_BY,
          }),
        ]}
        srcFor={srcFor}
      />,
    );
    expect(captions(container)).toEqual(['First · CC0', 'Second · CC0']);
  });
});

describe('ImageCredit on its own', () => {
  it('is the same rule the grid uses, so the hero cannot drift from it', () => {
    // Exported precisely so the single-image hero does not grow a second
    // version of this. If this ever disagrees with the grid, one of the two
    // pages is in breach and neither will say so.
    const { container } = render(<ImageCredit image={image()} />);
    expect(container.firstChild).toBeNull();

    const credited = render(
      <ImageCredit
        image={image({ attribution: 'A. Photographer', license_code: 'CC0', license_url: CC_BY })}
      />,
    );
    expect(captions(credited.container)).toEqual(['A. Photographer · CC0']);
  });
});
