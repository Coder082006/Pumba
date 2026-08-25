/**
 * What the map does when tiles do not arrive.
 *
 * The defect these exist for: the fallback was driven from a `try/catch`
 * around `import('maplibre-gl')` and `new maplibre.Map(...)`, but tiles are
 * fetched *after* construction resolves. So the one failure the fallback was
 * written for — a tile host that is blocked, rate-limited, or briefly
 * unresolvable — was the one failure it could never catch. What the user got
 * instead was `TypeError: Failed to fetch` and a MapLibre stack trace on the
 * page.
 *
 * Its own test passed throughout, because it asserted the server-rendered box
 * and the licence text and never exercised a tile at all. Same shape as the
 * other findings this phase: the mechanism existed, was tested in isolation,
 * and was connected to nothing that could reach it.
 *
 * `vi.mock` is hoisted and the component is imported statically on purpose.
 * The obvious alternative — `vi.doMock` plus `vi.resetModules()` and a dynamic
 * import — gives the component a *second copy of React*, whose effects never
 * run under the test renderer, so both tests below pass vacuously.
 */

import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Map } from './map';

/** MapLibre event name -> the handler the component registered. */
const handlers: Record<string, (event: unknown) => void> = {};

vi.mock('maplibre-gl', () => ({
  Map: class {
    on(event: string, handler: (event: unknown) => void) {
      handlers[event] = handler;
    }
    remove() {}
  },
  Marker: class {
    setLngLat() {
      return this;
    }
    addTo() {
      return this;
    }
    getElement() {
      return document.createElement('div');
    }
  },
  LngLatBounds: class {
    extend() {}
  },
}));

const PIN = { id: 'a', latitude: -6.16, longitude: 39.19, label: 'Stone Town' };

function renderMap() {
  return render(
    <Map
      tileUrl="https://tiles.example/{z}/{x}/{y}.png"
      attribution="© Example contributors"
      pins={[PIN]}
    />,
  );
}

beforeEach(() => {
  for (const key of Object.keys(handlers)) delete handlers[key];
});

describe('when tiles cannot be fetched', () => {
  it('subscribes to the error MapLibre reports them on', async () => {
    // Without this subscription the rest is unreachable: the rejection lands
    // outside the component and surfaces as an overlay.
    renderMap();
    await waitFor(() => expect(handlers.error).toBeDefined());
  });

  it('shows the stated fallback rather than an unhandled rejection', async () => {
    renderMap();
    await waitFor(() => expect(handlers.error).toBeDefined());

    act(() => handlers.error?.({}));

    expect(await screen.findByText(/map could not be loaded/i)).toBeDefined();
  });

  it('keeps the attribution, which is a licence term either way', async () => {
    const { container } = renderMap();
    await waitFor(() => expect(handlers.error).toBeDefined());
    act(() => handlers.error?.({}));

    expect(container.textContent).toContain('© Example contributors');
  });
});

describe('once tiles have loaded', () => {
  it('keeps the map when a later tile fails', async () => {
    // Guards the guard. "Show the fallback on any error" would pass every test
    // above while blanking a map the tourist is already reading, the moment a
    // single tile 404s at the edge of coverage.
    renderMap();
    await waitFor(() => expect(handlers.sourcedata).toBeDefined());

    act(() => handlers.sourcedata?.({ sourceId: 'base', isSourceLoaded: true }));
    act(() => handlers.error?.({}));

    expect(screen.queryByText(/map could not be loaded/i)).toBeNull();
  });

  it('ignores a load signal from a different source', async () => {
    // `isSourceLoaded` is emitted for every source. Treating any of them as
    // proof the base tiles arrived would re-open the case above.
    renderMap();
    await waitFor(() => expect(handlers.sourcedata).toBeDefined());

    act(() => handlers.sourcedata?.({ sourceId: 'something-else', isSourceLoaded: true }));
    act(() => handlers.error?.({}));

    expect(await screen.findByText(/map could not be loaded/i)).toBeDefined();
  });
});
