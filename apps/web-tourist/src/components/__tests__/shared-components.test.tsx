/**
 * The shared components exist to make three SRS rules unviolatable rather than
 * documented, so these tests assert the rules and not the markup.
 *
 *   §7.2  money is a decimal string and never a float;
 *         timestamps render in the *destination's* zone, not the viewer's.
 *   §29   NFR-P01's CLS budget: every image and the map reserve their box
 *         before anything loads.
 *   §7.3  media order is the server's — primary first, then `sort_order`.
 *
 * Tested from the consuming application rather than inside `@pumba/ui`,
 * because that is how they are used and `web-tourist` already has jsdom and
 * Testing Library configured.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Gallery, LocalTime, Money, type GalleryImage } from '@pumba/ui';

describe('Money', () => {
  it('formats from the decimal string without a float round-trip', () => {
    // 0.1 + 0.2 is the canonical float failure. This amount has more
    // significant digits than a double can hold exactly, so a number
    // conversion anywhere in the path would show a different figure.
    render(<Money value={{ amount: '10000000000000000.05', currency: 'USD' }} locale="en-GB" />);
    expect(screen.getByText(/10,000,000,000,000,000\.05/)).toBeDefined();
  });

  it('falls back to the raw string rather than a wrong figure', () => {
    render(<Money value={{ amount: '12.50', currency: 'NOTACURRENCY' }} locale="en-GB" />);
    expect(screen.getByText('NOTACURRENCY 12.50')).toBeDefined();
  });
});

describe('LocalTime', () => {
  // 08:30 in Zanzibar (UTC+3) is 05:30 UTC. A viewer in London must still
  // read 08:30, which is the whole point of §7.2 — and the number that would
  // silently be wrong if the component used the browser's zone.
  const departure = '2027-08-12T05:30:00Z';

  it('renders in the destination zone, not the runtime zone', () => {
    render(
      <LocalTime value={departure} timeZone="Africa/Dar_es_Salaam" display="time" locale="en-GB" />,
    );
    expect(screen.getByText('08:30')).toBeDefined();
  });

  it('renders the same instant differently for a different destination', () => {
    render(<LocalTime value={departure} timeZone="Europe/London" display="time" locale="en-GB" />);
    expect(screen.getByText('06:30')).toBeDefined();
  });

  it('carries the unambiguous instant in the datetime attribute', () => {
    const { container } = render(
      <LocalTime value={departure} timeZone="Africa/Dar_es_Salaam" display="time" />,
    );
    expect(container.querySelector('time')?.getAttribute('dateTime')).toBe(
      '2027-08-12T05:30:00.000Z',
    );
  });

  it('falls back to UTC and says so when the zone is unknown', () => {
    // Never to the viewer's zone: that would be wrong without looking wrong.
    render(<LocalTime value={departure} timeZone="Mars/Olympus" display="time" locale="en-GB" />);
    expect(screen.getByText(/05:30 UTC/)).toBeDefined();
  });

  it('shows the raw value rather than "Invalid Date"', () => {
    render(<LocalTime value="not-a-timestamp" timeZone="Africa/Dar_es_Salaam" />);
    expect(screen.getByText('not-a-timestamp')).toBeDefined();
  });
});

describe('Gallery', () => {
  const image = (over: Partial<GalleryImage>): GalleryImage => ({
    file_key: 'img/a',
    alt_text: 'A beach',
    width: 1200,
    height: 800,
    is_primary: false,
    sort_order: 10,
    // Own work: `license_code: ''` renders no credit, which keeps these
    // assertions about layout rather than about provenance. The credit rule
    // itself is tested in `packages/ui`, beside the component.
    attribution: '',
    license_code: '',
    license_url: '',
    source_url: '',
    ...over,
  });
  const srcFor = (key: string) => `https://cdn.example/${key}`;

  it('gives every image its intrinsic box so nothing shifts on load', () => {
    render(
      <Gallery
        images={[image({ file_key: 'img/a' }), image({ file_key: 'img/b' })]}
        srcFor={srcFor}
      />,
    );
    for (const img of screen.getAllByRole('img')) {
      expect(img.getAttribute('width')).toBe('1200');
      expect(img.getAttribute('height')).toBe('800');
    }
  });

  it('puts the primary image first whatever order it arrives in', () => {
    render(
      <Gallery
        images={[
          image({ file_key: 'img/second', sort_order: 5, alt_text: 'Second' }),
          image({ file_key: 'img/primary', is_primary: true, sort_order: 99, alt_text: 'Primary' }),
        ]}
        srcFor={srcFor}
      />,
    );
    const [firstImage] = screen.getAllByRole('img');
    expect(firstImage?.getAttribute('alt')).toBe('Primary');
  });

  it('lazy-loads everything after the first', () => {
    render(
      <Gallery
        images={[image({ file_key: 'img/a' }), image({ file_key: 'img/b' })]}
        srcFor={srcFor}
        priority
      />,
    );
    const [first, second] = screen.getAllByRole('img');
    expect(first?.getAttribute('loading')).toBe('eager');
    expect(second?.getAttribute('loading')).toBe('lazy');
  });

  it('marks an undescribed image decorative instead of reading out a file name', () => {
    const { container } = render(
      <Gallery images={[image({ alt_text: '   ' })]} srcFor={srcFor} />,
    );
    const img = container.querySelector('img');
    expect(img?.getAttribute('alt')).toBe('');
    expect(img?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders nothing at all when there is no media', () => {
    const { container } = render(<Gallery images={[]} srcFor={srcFor} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('Map', () => {
  it('reserves its box and shows attribution before any script runs', async () => {
    // MapLibre needs WebGL, which jsdom does not have. What matters here is
    // what the server renders — the reserved box and the licence text — so
    // the module is stubbed and the effect is allowed to fail.
    vi.doMock('maplibre-gl', () => {
      throw new Error('no WebGL in jsdom');
    });
    const { Map } = await import('@pumba/ui');

    const { container } = render(
      <Map
        tileUrl="https://tiles.example/{z}/{x}/{y}.png"
        attribution="© Example contributors"
        pins={[{ id: 'a', latitude: -6.16, longitude: 39.19, label: 'Stone Town' }]}
        aspectRatio="4 / 3"
      />,
    );

    const region = screen.getByRole('region', { name: 'Map' });
    // The ratio is on the server-rendered wrapper, so the space is held before
    // hydration. Without it the map mounts into a zero-height div and pushes
    // the page down — the easiest possible way to fail §29's CLS < 0.1.
    expect(region.getAttribute('style')).toContain('4 / 3');
    // Attribution is a licence term, so it renders whether or not tiles load.
    expect(container.textContent).toContain('© Example contributors');
  });
});
