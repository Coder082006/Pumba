/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The shared UI package ships TypeScript source rather than a build step.
  transpilePackages: ['@pumba/ui', '@pumba/contracts'],
  eslint: { ignoreDuringBuilds: true }, // lint runs as its own CI job

  /**
   * Serve blocking metadata to everyone — SRS §24.8.
   *
   * Next 15.2+ streams metadata instead of blocking the initial flush, and
   * only emits it inside `<head>` for user agents matching its built-in
   * `HTML_LIMITED_BOT_UA_RE`. For everyone else the tags are written into the
   * body on the assumption that the client hoists them.
   *
   * **Measured against a production build with headless Chrome
   * (`--dump-dom`), after full hydration: they are not hoisted.** On every
   * page using an async `generateMetadata` — destination, attraction and
   * activity, which §24.8 designates as the platform's SEO surface — the
   * `<title>`, `<meta name="description">` and `rel=canonical` sit in the body
   * and never reach `<head>`. Lighthouse scored `meta-description` 0 on
   * `/destinations/stone-town` for exactly this reason, and it was right to.
   *
   * `shouldServeStreamingMetadata` (next/dist/server/lib/streaming-metadata.js)
   * treats this value as the regex of agents that get blocking metadata, so a
   * pattern matching everything disables streaming for the whole app. The cost
   * is that the first flush waits for `generateMetadata` to resolve — which
   * here awaits the same cached destination fetch the page body already needs,
   * so it is a wait we were making anyway.
   *
   * The alternative was to give Lighthouse a `Chrome-Lighthouse` user agent so
   * it received the blocking version. That would have turned the gate green
   * while leaving every real browser and every JS-executing crawler with a
   * page whose head has no description — passing the check by no longer
   * asking the question.
   */
  htmlLimitedBots: /.*/,
};

export default nextConfig;
