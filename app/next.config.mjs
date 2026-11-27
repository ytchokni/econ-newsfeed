/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV !== "production";

// Canonical origin for search engines; keep the default in sync with
// DEFAULT_SITE_URL in src/lib/seo.ts.
const canonicalOrigin = (() => {
  try {
    return new URL(process.env.SITE_URL || "https://www.econnewsfeed.org").origin;
  } catch {
    return "https://www.econnewsfeed.org";
  }
})();
const canonicalHost = new URL(canonicalOrigin).host;

// Other production hosts serving this same app. Their pages 308 to the
// canonical host so search engines index one copy. /api/* (NextAuth
// callbacks, proxied backend calls, email unsubscribe POSTs) is left alone
// so in-flight sign-ins and links keep working on either host. Vercel preview
// URLs (econ-newsfeed-git-*.vercel.app) are not listed: they stay reachable
// and are marked noindex below instead.
const legacyHosts = ["econ-newsfeed.vercel.app", "econnewsfeed.org"].filter(
  (host) => host !== canonicalHost,
);

// Vercel sets VERCEL_ENV to "preview" or "development" outside production.
const isNonProductionDeployment =
  Boolean(process.env.VERCEL_ENV) && process.env.VERCEL_ENV !== "production";

const nextConfig = {
  output: "standalone",

  async rewrites() {
    return [
      {
        source: "/api/:path((?!auth|admin).*)",
        destination: `${process.env.API_INTERNAL_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },

  async redirects() {
    return legacyHosts.map((host) => ({
      source: "/:path((?!api/).*)",
      has: [{ type: "host", value: host.replace(/\./g, "\\.") }],
      destination: `${canonicalOrigin}/:path`,
      permanent: true,
    }));
  },

  async headers() {
    return [
      ...(isNonProductionDeployment
        ? [{ source: "/(.*)", headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }] }]
        : []),
      {
        source: "/api/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex" }],
      },
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: https:",
              "font-src 'self'",
              "connect-src 'self' https://accounts.google.com",
              "frame-ancestors 'none'",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
