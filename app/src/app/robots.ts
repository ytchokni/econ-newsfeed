import type { MetadataRoute } from "next";
import { getCanonicalOrigin, isIndexableDeployment } from "@/lib/seo";

export function buildRobots(env: NodeJS.ProcessEnv = process.env): MetadataRoute.Robots {
  if (!isIndexableDeployment(env)) {
    // Preview / development deployments on Vercel: keep crawlers out entirely.
    return { rules: { userAgent: "*", disallow: "/" } };
  }
  const origin = getCanonicalOrigin(env);
  return {
    rules: {
      userAgent: "*",
      // Longest match wins, so these stay crawlable despite "/*?" below.
      allow: ["/", "/_next/", "/og-image.png"],
      disallow: [
        "/admin",
        "/api/",
        "/settings",
        "/unsubscribe",
        // Filter, search and pagination variants of the feed and directory.
        "/*?",
      ],
    },
    sitemap: `${origin}/sitemap.xml`,
    host: origin,
  };
}

export default function robots(): MetadataRoute.Robots {
  return buildRobots();
}
