import type { NextConfig } from "next";

/**
 * Production: the browser talks only to this site. `/api/*` is proxied to the backend (API_ORIGIN, e.g. the Render
 * URL), so the session cookie is first-party and no cross-site cookie or CORS is involved. Build with
 * NEXT_PUBLIC_API_URL=/api. Unset locally, where the browser calls the API directly (see lib/api/config.ts).
 */
const apiOrigin = (process.env.API_ORIGIN ?? '').replace(/\/+$/, '')

const nextConfig: NextConfig = {
  async rewrites() {
    return apiOrigin ? [{ source: '/api/:path*', destination: `${apiOrigin}/:path*` }] : []
  },
  experimental: {
    agentFeedback: true,
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
