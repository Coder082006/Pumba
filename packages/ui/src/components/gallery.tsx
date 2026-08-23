import { cn } from '../lib/cn';

/**
 * A media gallery — SRS §7.3 `media`, §29 (NFR-P01), §35.7.
 *
 * **Every image reserves its box.** `width` and `height` come from the payload
 * and are set as attributes, so the browser computes the aspect ratio before a
 * single byte of image data arrives. Without them each image is zero-height
 * until it loads and then pushes the page down — the same CLS failure the map
 * avoids, multiplied by the number of photographs. That the API sends both is
 * why it can be done here rather than guessed.
 *
 * **The order is the server's.** §7.3 gives `media` a `sort_order` and an
 * `is_primary` flag, and the API already returns them ordered by
 * `domain.media.order_media`. This re-sorts defensively rather than trusting
 * array order to survive a cache layer or a client-side merge, but it does not
 * invent an ordering — primary first, then `sort_order`, which is what the
 * domain function does.
 *
 * **`alt` is never empty by accident.** A curated photograph with no
 * description is an accessibility defect, and `alt_text` is non-null in the
 * payload. Where it is genuinely blank the image is marked decorative
 * (`alt=""` plus `aria-hidden`) rather than left to a screen reader to
 * announce as a file name.
 */
export interface GalleryImage {
  file_key: string;
  alt_text: string;
  width: number;
  height: number;
  is_primary: boolean;
  sort_order: number;
}

export interface GalleryProps {
  images: GalleryImage[];
  /**
   * Turns a `file_key` into a URL. Injected because §35.7 makes the key
   * content-hashed and the CDN host is environment configuration — a
   * component that built the URL itself would hardcode one.
   */
  srcFor: (fileKey: string) => string;
  /**
   * `eager` on the first image only where the gallery is above the fold.
   * Everything after the first is always lazy.
   */
  priority?: boolean;
  className?: string;
}

/** Primary first, then `sort_order`, then `file_key` for a total order. */
function ordered(images: GalleryImage[]): GalleryImage[] {
  return [...images].sort((a, b) => {
    if (a.is_primary !== b.is_primary) return a.is_primary ? -1 : 1;
    if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
    return a.file_key.localeCompare(b.file_key);
  });
}

export function Gallery({ images, srcFor, priority = false, className }: GalleryProps) {
  const shown = ordered(images);
  if (shown.length === 0) return null;

  return (
    <ul className={cn('grid grid-cols-2 gap-2 sm:grid-cols-3', className)}>
      {shown.map((image, index) => {
        const decorative = image.alt_text.trim() === '';
        return (
          <li key={image.file_key} className={cn(index === 0 && 'col-span-2 row-span-2')}>
            <img
              src={srcFor(image.file_key)}
              alt={decorative ? '' : image.alt_text}
              aria-hidden={decorative || undefined}
              width={image.width}
              height={image.height}
              // The first image of an above-the-fold gallery is usually the
              // LCP element, so it must not be lazy. Everything else must be.
              loading={priority && index === 0 ? 'eager' : 'lazy'}
              decoding="async"
              className="h-full w-full rounded-md object-cover"
            />
          </li>
        );
      })}
    </ul>
  );
}
