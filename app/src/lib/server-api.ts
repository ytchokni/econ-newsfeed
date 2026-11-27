// Server-side fetches from the backend API (pages, sitemap). Not for client code.
import { headers } from "next/headers";
import { internalApiHeaders, internalApiKey, visitorIp } from "./internal-api";

export function apiBase(env: NodeJS.ProcessEnv = process.env): string {
  return env.API_INTERNAL_URL || "http://localhost:8000";
}

/**
 * Headers for server-side backend calls that serve no particular visitor
 * (sitemap). With INTERNAL_API_KEY (same value as on the API) the backend
 * treats them as the frontend itself: no rate limit, no paging caps.
 */
export function backendHeaders(env: NodeJS.ProcessEnv = process.env): Record<string, string> {
  return internalApiHeaders(undefined, env);
}

/**
 * Headers for backend calls made while rendering a page for a visitor. They
 * name the visitor, so walking every paper/researcher page counts against that
 * visitor's rate limit (backend/api_protection.py). Without INTERNAL_API_KEY
 * they are empty and the request headers are not read, so caching is unchanged.
 */
export async function visitorBackendHeaders(): Promise<Record<string, string>> {
  if (!internalApiKey()) return {};
  return internalApiHeaders({ ip: visitorIp(await headers()), source: "ssr" });
}

/**
 * GET a backend JSON resource. Returns null only on 404, so missing records
 * become real 404 pages; any other failure (including a 429) throws (a 5xx
 * page), which tells crawlers to retry instead of dropping the URL.
 * `forVisitor` attributes the call to the visitor being rendered for.
 */
export async function fetchBackend<T>(
  path: string,
  revalidate: number,
  { forVisitor = false }: { forVisitor?: boolean } = {},
): Promise<T | null> {
  const res = await fetch(`${apiBase()}${path}`, {
    headers: forVisitor ? await visitorBackendHeaders() : backendHeaders(),
    next: { revalidate },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Backend returned ${res.status} for ${path}`);
  return res.json() as Promise<T>;
}
