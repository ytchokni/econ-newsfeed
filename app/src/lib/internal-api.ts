// Headers that let the backend tell frontend traffic apart from direct API
// calls (see backend/api_protection.py). The backend trusts X-Visitor-IP only
// when X-Internal-Api-Key matches its INTERNAL_API_KEY. Server-side only: the key
// must never reach the browser, so it is read from a non-NEXT_PUBLIC env var.

export const INTERNAL_KEY_HEADER = "x-internal-api-key";
export const VISITOR_IP_HEADER = "x-visitor-ip";
export const VISITOR_SOURCE_HEADER = "x-visitor-source";

export type VisitorSource = "proxy" | "ssr";

interface HeaderReader {
  get(name: string): string | null;
}

export function internalApiKey(): string {
  return (process.env.INTERNAL_API_KEY || "").trim();
}

/** The visitor's IP as Vercel reports it (Vercel overwrites both headers). */
export function visitorIp(headers: HeaderReader): string {
  const real = headers.get("x-real-ip")?.trim();
  if (real) return real;
  const first = headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return first || "unknown";
}

/**
 * Headers for a backend call made by the frontend's server. Pass the visitor
 * the call is made for, so per-visitor limits apply; omit it only for calls
 * that serve no particular visitor. Empty when INTERNAL_API_KEY is unset, so
 * the site keeps working (with coarser limits) before the secret exists.
 */
export function internalApiHeaders(
  visitor?: { ip: string; source: VisitorSource },
): Record<string, string> {
  const key = internalApiKey();
  if (!key) return {};
  const headers: Record<string, string> = { [INTERNAL_KEY_HEADER]: key };
  if (visitor) {
    headers[VISITOR_IP_HEADER] = visitor.ip;
    headers[VISITOR_SOURCE_HEADER] = visitor.source;
  }
  return headers;
}
