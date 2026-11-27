import { fetchBackend } from "@/lib/server-api";
import { absoluteUrl, getCanonicalOrigin } from "@/lib/seo";
import {
  SITEMAP_HEADERS,
  STATIC_PATHS,
  buildUrlset,
  chunkEntries,
  parseSitemapFile,
  type SitemapChunk,
} from "@/lib/sitemap";

export async function GET(_req: Request, { params }: { params: Promise<{ file: string }> }) {
  const parsed = parseSitemapFile((await params).file);
  if (!parsed) return new Response("Not found", { status: 404 });

  const origin = getCanonicalOrigin();
  if (parsed.kind === "pages") {
    const body = buildUrlset(STATIC_PATHS.map((path) => ({ loc: absoluteUrl(path, origin) })));
    return new Response(body, { headers: SITEMAP_HEADERS });
  }

  let chunk: SitemapChunk | null;
  try {
    chunk = await fetchBackend<SitemapChunk>(`/api/sitemap/${parsed.kind}?page=${parsed.page}`, 3600);
  } catch {
    return new Response("Sitemap temporarily unavailable", {
      status: 503,
      headers: { "Retry-After": "3600", "Cache-Control": "no-store" },
    });
  }
  if (!chunk || chunk.items.length === 0) return new Response("Not found", { status: 404 });
  return new Response(buildUrlset(chunkEntries(parsed.kind, chunk, origin)), {
    headers: SITEMAP_HEADERS,
  });
}
