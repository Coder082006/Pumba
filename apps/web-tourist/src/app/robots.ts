import type { MetadataRoute } from 'next';

/**
 * `robots.txt` — SRS §24.8.
 *
 * Allows the catalogue and disallows everything that is either a private
 * surface or a way to spend a single-use token.
 *
 * **`/verify-email` is disallowed for a concrete reason, not for tidiness.**
 * The verification token arrives in a URL, and `/auth/verify-email` consumes
 * it on first use. Anything that fetches that URL before the person does —
 * a crawler, a link prefetcher, a mail scanner — burns the token, and the
 * real click then lands on "already used". The page already defends against
 * the prefetch case by verifying from an effect rather than during render;
 * this is the other half, and both are needed because they stop different
 * things.
 *
 * `/stays` is disallowed because it is a planning tool operating on a
 * tourist's own trip, not an SEO surface — the destination, attraction and
 * activity pages are what §24.8 means a crawler to find, and they link to it.
 *
 * A `robots.txt` is a request, not an access control. Nothing here is a
 * security measure: the authorisation tests are (§30, and every endpoint's
 * "a foreign principal receives 404 not 403" case).
 */
export default function robots(): MetadataRoute.Robots {
  const base = process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000';

  return {
    rules: {
      userAgent: '*',
      allow: '/',
      disallow: [
        // The (auth) route group — the paths, not the group name, since a
        // group contributes no URL segment.
        '/login',
        '/register',
        '/forgot-password',
        '/reset-password',
        '/verify-email',
        '/stays',
      ],
    },
    sitemap: `${base}/sitemap.xml`,
  };
}
