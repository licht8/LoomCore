/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  typescript: {
    // The build must not fail on type errors, but they are still gated: CI runs
    // `npx tsc --noEmit` (.github/workflows/ci.yml) as a required check.
    ignoreBuildErrors: true,
  },
  // Leave image optimization enabled globally. There is no `next/image` usage
  // yet; opt out per image with the `unoptimized` prop if raw bytes are needed.
}

export default nextConfig
