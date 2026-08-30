/**
 * Descriptions a catalogue page can always carry — SRS §24.8.
 *
 * §24.8 makes destination, attraction and activity pages the platform's SEO
 * surface. All three built their meta description the same way:
 *
 *     description: subject.summary ?? undefined
 *
 * and all three fell back, on failure, to `return { title: '…' }` with no
 * description at all. Two ordinary situations therefore produced a page with
 * no meta description:
 *
 * * **`summary` is null.** It is nullable on every one of the three models,
 *   and legitimately empty for a listing whose copy nobody has written yet.
 * * **The metadata fetch failed.** `generateMetadata` runs concurrently with
 *   the page body rather than sharing its result, so the body can render
 *   perfectly while this half fails — and with `revalidate = 30` the
 *   description-less page is then served to every crawler for half a minute.
 *
 * The Lighthouse SEO gate caught the second on `/destinations/stone-town`:
 * `meta-description` scored 0 while the page itself scored a clean 200 with
 * full marks for performance and accessibility, which is exactly what a
 * half-failed render looks like from the outside.
 *
 * **These say nothing the platform does not know.** Each describes what the
 * *page* contains, not what the place is like — the difference between a
 * fallback and a fabrication, and the same line the JSON-LD on the destination
 * page draws when it omits `description` rather than inventing one. A
 * subject's own summary is always preferred when there is one.
 */

export type CatalogueEntity = 'destination' | 'attraction' | 'activity';

/**
 * @param entity Which kind of page this is.
 * @param name   The subject's name, where the fetch that would have supplied
 *               it succeeded. Absent on the failure path, which is why every
 *               branch has to read correctly without one.
 */
export function fallbackDescription(entity: CatalogueEntity, name?: string): string {
  switch (entity) {
    case 'destination':
      return (
        `Attractions, activities and places to stay${name ? ` in ${name}` : ''}, ` +
        'with the transfers between them planned around your days.'
      );
    case 'attraction':
      return (
        `${name ?? 'This attraction'} — what there is to see, opening hours where ` +
        'the venue publishes them, and how it fits into a day of your trip.'
      );
    case 'activity':
      return (
        `${name ?? 'This activity'} — what it includes, who it suits, and how to ` +
        'fit it into your itinerary.'
      );
  }
}
