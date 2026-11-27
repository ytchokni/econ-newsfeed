import { fetchBackend } from "@/lib/server-api";
import { getCanonicalOrigin } from "@/lib/seo";
import {
  SITEMAP_HEADERS,
  buildSitemapIndex,
  sitemapFiles,
  type SitemapIndexData,
} from "@/lib/sitemap";

// Built per request (cached by the CDN via SITEMAP_HEADERS), never at build
// time, so a backend hiccup during a deploy can't freeze a broken sitemap.
export const dynamic = "force-dynamic";

export async function GET() {
  let index: SitemapIndexData | null;
  try {
    index = await fetchBackend<SitemapIndexData>("/api/sitemap", 3600);
  } catch {
    index = null;
  }
  if (!index) {
    // Temporary: crawlers retry a 503 instead of treating the sitemap as gone.
    return new Response("Sitemap temporarily unavailable", {
      status: 503,
      headers: { "Retry-After": "3600", "Cache-Control": "no-store" },
    });
  }
  const origin = getCanonicalOrigin();
  const body = buildSitemapIndex(
    sitemapFiles(index).map((file) => ({ loc: `${origin}/sitemaps/${file}` })),
  );
  return new Response(body, { headers: SITEMAP_HEADERS });
}
