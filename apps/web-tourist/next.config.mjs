/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The shared UI package ships TypeScript source rather than a build step.
  transpilePackages: ['@pumba/ui', '@pumba/contracts'],
  eslint: { ignoreDuringBuilds: true }, // lint runs as its own CI job
};

export default nextConfig;
