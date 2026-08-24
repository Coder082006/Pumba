import { Map, type MapPin } from '@pumba/ui';

import { mapConfig } from '@/lib/config';

/**
 * A map, or a visible explanation of why there isn't one.
 *
 * `GET /config` is on the critical path of every screen that shows a map
 * (ADR 0016 puts the tile URL and its attribution there), so a failure or a
 * slow response has the map as its visible casualty. The failure mode this
 * component exists to prevent is the quiet one: a reserved box that never
 * mounts anything looks exactly like a layout bug, so nobody reports it as an
 * outage and the pin a tourist needed is simply absent.
 *
 * So an unreachable `/config` renders a **stated** degraded panel — same
 * reserved box, an explicit message, and the coordinates in text. The
 * information the map was carrying is still on the page; only the picture is
 * missing, and the page says so.
 *
 * `<Map>` handles the other failure — config arrived but the tiles did not —
 * with its own message inside the same box. Two different causes, both
 * visible, neither silent.
 */
export interface MapPanelProps {
  pins: MapPin[];
  center?: { latitude: number; longitude: number } | undefined;
  zoom?: number | undefined;
  aspectRatio?: string | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

export async function MapPanel({ pins, center, zoom, aspectRatio, title, className }: MapPanelProps) {
  let config: Awaited<ReturnType<typeof mapConfig>>;
  try {
    config = await mapConfig();
  } catch {
    return (
      <MapUnavailable pins={pins} aspectRatio={aspectRatio} title={title} className={className} />
    );
  }

  return (
    <Map
      tileUrl={config.tileUrl}
      attribution={config.attribution}
      pins={pins}
      center={center}
      zoom={zoom}
      aspectRatio={aspectRatio}
      title={title}
      className={className}
    />
  );
}

function MapUnavailable({
  pins,
  aspectRatio = '16 / 9',
  title = 'Map',
  className,
}: Omit<MapPanelProps, 'center' | 'zoom'>) {
  return (
    <figure
      className={className}
      role="region"
      aria-label={`${title} — unavailable`}
      // Same reserved box as the real map, so the degraded state does not
      // shift the page either. §29's CLS budget does not get a holiday
      // because something failed.
      style={{ aspectRatio }}
    >
      <div className="flex h-full w-full flex-col justify-center gap-2 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-600">
        <p className="font-medium text-slate-700">The map is temporarily unavailable.</p>
        {/* The point of the degraded state: the locations are still here. */}
        <ul className="space-y-1">
          {pins.map((pin) => (
            <li key={pin.id}>
              {pin.label} —{' '}
              <span className="tabular-nums">
                {pin.latitude.toFixed(4)}, {pin.longitude.toFixed(4)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </figure>
  );
}
