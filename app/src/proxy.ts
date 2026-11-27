import { NextResponse, type NextRequest } from "next/server";
import {
  INTERNAL_KEY_HEADER,
  VISITOR_IP_HEADER,
  VISITOR_SOURCE_HEADER,
  internalApiHeaders,
  visitorIp,
} from "@/lib/internal-api";

// Browser calls to /api/* are rewritten to the backend by next.config.mjs.
// Request headers set here travel with the rewrite, so the backend can apply
// rate limits to the real visitor instead of Vercel's egress IPs.
// (Next 16 names this file proxy.ts; it used to be middleware.ts.)

let warnedMissingKey = false;

export function proxy(request: NextRequest) {
  const internal = internalApiHeaders({ ip: visitorIp(request.headers), source: "proxy" });
  if (!internal[INTERNAL_KEY_HEADER]) {
    if (!warnedMissingKey) {
      warnedMissingKey = true;
      console.warn("INTERNAL_API_KEY is not set; /api requests reach the backend without visitor IPs.");
    }
    return NextResponse.next();
  }

  const headers = new Headers(request.headers);
  // Never pass through values a client supplied for these.
  headers.delete(INTERNAL_KEY_HEADER);
  headers.delete(VISITOR_IP_HEADER);
  headers.delete(VISITOR_SOURCE_HEADER);
  for (const [name, value] of Object.entries(internal)) headers.set(name, value);
  return NextResponse.next({ request: { headers } });
}

// Same paths as the /api rewrite in next.config.mjs (auth and admin are route
// handlers in this app, not proxied).
export const config = {
  matcher: "/api/:path((?!auth|admin).*)",
};
