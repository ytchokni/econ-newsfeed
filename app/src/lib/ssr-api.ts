import { headers } from "next/headers";
import { internalApiHeaders, internalApiKey, visitorIp } from "./internal-api";

/**
 * Headers for backend calls made while rendering a page, naming the visitor
 * so that walking every paper/researcher page counts against their own limit.
 * Without INTERNAL_API_KEY it returns no headers and does not read the request
 * headers, so the page keeps its previous caching behaviour.
 */
export async function ssrApiHeaders(): Promise<Record<string, string>> {
  if (!internalApiKey()) return {};
  const requestHeaders = await headers();
  return internalApiHeaders({ ip: visitorIp(requestHeaders), source: "ssr" });
}

/** Thrown when the backend rate-limits a page render, so the page fails with a
 * 500 (retry later) rather than a 404 that tells crawlers the page is gone. */
export class BackendRateLimitedError extends Error {
  constructor(path: string) {
    super(`Backend rate limit reached for ${path}`);
    this.name = "BackendRateLimitedError";
  }
}
