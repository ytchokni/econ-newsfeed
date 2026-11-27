// Server-side fetches from the backend API (pages, sitemap). Not for client code.

export function apiBase(env: NodeJS.ProcessEnv = process.env): string {
  return env.API_INTERNAL_URL || "http://localhost:8000";
}

/**
 * Headers for server-side backend calls. INTERNAL_API_KEY (same value as on
 * the API) exempts page rendering from the backend's per-IP rate limit, which
 * all server-side requests from the frontend host share.
 */
export function backendHeaders(env: NodeJS.ProcessEnv = process.env): Record<string, string> {
  const key = (env.INTERNAL_API_KEY || "").trim();
  return key ? { "X-Internal-Api-Key": key } : {};
}

/**
 * GET a backend JSON resource. Returns null only on 404, so missing records
 * become real 404 pages; any other failure throws (a 5xx page), which tells
 * crawlers to retry instead of dropping the URL.
 */
export async function fetchBackend<T>(path: string, revalidate: number): Promise<T | null> {
  const res = await fetch(`${apiBase()}${path}`, {
    headers: backendHeaders(),
    next: { revalidate },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Backend returned ${res.status} for ${path}`);
  return res.json() as Promise<T>;
}
