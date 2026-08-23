/**
 * `GET /config` is on the critical path of every screen that shows a map, so
 * its failure has the map as the visible casualty.
 *
 * The failure mode worth testing is the *quiet* one: a reserved box that never
 * mounts anything looks exactly like a layout bug, so it gets ignored rather
 * than reported, and the pin a tourist needed is simply absent. These tests
 * assert the degraded state is stated on the page and still carries the
 * locations.
 */

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mapConfig = vi.fn();

vi.mock('@/lib/map-config', () => ({ mapConfig: () => mapConfig() }));
const PINS = [{ id: 'a', latitude: -6.1631, longitude: 39.1892, label: 'Stone Town' }];

describe('MapPanel when /config is unreachable', () => {
  beforeEach(() => {
    mapConfig.mockReset();
  });

  it('says so, rather than leaving an empty box', async () => {
    mapConfig.mockRejectedValue(new Error('config unreachable'));
    const { MapPanel } = await import('@/components/catalogue/map-panel');

    render(await MapPanel({ pins: PINS }));

    expect(screen.getByText(/temporarily unavailable/i)).toBeDefined();
  });

  it('still shows where the places are', async () => {
    // The whole point of a degraded state rather than a blank one: the
    // information the map was carrying is still on the page.
    mapConfig.mockRejectedValue(new Error('config unreachable'));
    const { MapPanel } = await import('@/components/catalogue/map-panel');

    render(await MapPanel({ pins: PINS }));

    expect(screen.getByText(/Stone Town/)).toBeDefined();
    expect(screen.getByText(/-6\.1631, 39\.1892/)).toBeDefined();
  });

  it('reserves the same box it would have used', async () => {
    // A degraded state that collapses shifts the page, which fails §29's CLS
    // budget exactly as a zero-height map would.
    mapConfig.mockRejectedValue(new Error('config unreachable'));
    const { MapPanel } = await import('@/components/catalogue/map-panel');

    render(await MapPanel({ pins: PINS, aspectRatio: '4 / 3' }));

    const region = screen.getByRole('region', { name: /unavailable/i });
    expect(region.getAttribute('style')).toContain('4 / 3');
  });

  it('renders the real map when config resolves', async () => {
    mapConfig.mockResolvedValue({
      tileUrl: 'https://tiles.example/{z}/{x}/{y}.png',
      attribution: '© Example contributors',
    });
    const { MapPanel } = await import('@/components/catalogue/map-panel');

    const { container } = render(await MapPanel({ pins: PINS }));

    // Attribution renders from the config that arrived, and no degraded
    // message is present — so the two states are genuinely exclusive.
    expect(container.textContent).toContain('© Example contributors');
    expect(screen.queryByText(/temporarily unavailable/i)).toBeNull();
  });
});
