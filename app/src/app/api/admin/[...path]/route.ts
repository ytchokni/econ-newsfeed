import { NextRequest, NextResponse } from "next/server";
import { createHmac, timingSafeEqual } from "crypto";

const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const SCRAPE_API_KEY = process.env.SCRAPE_API_KEY || "";
const API_INTERNAL_URL =
  process.env.API_INTERNAL_URL || "http://localhost:8000";
const COOKIE_NAME = "admin_session";
const COOKIE_MAX_AGE = 7 * 24 * 60 * 60; // 7 days in seconds

function verifyToken(token: string): boolean {
  const [timestampStr, signature] = token.split(".");
  if (!timestampStr || !signature) return false;

  const timestamp = Number(timestampStr);
  if (isNaN(timestamp)) return false;

  const ageSeconds = (Date.now() - timestamp) / 1000;
  if (ageSeconds > COOKIE_MAX_AGE) return false;

  const expected = createHmac("sha256", ADMIN_PASSWORD)
    .update(timestampStr)
    .digest("hex");
  const sigBuf = Buffer.from(signature, "utf-8");
  const expBuf = Buffer.from(expected, "utf-8");
  if (sigBuf.length !== expBuf.length) return false;
  return timingSafeEqual(sigBuf, expBuf);
}

function unauthorizedOrMisconfigured(request: NextRequest): NextResponse | null {
  if (!ADMIN_PASSWORD || !SCRAPE_API_KEY) {
    return NextResponse.json({ error: "Not configured" }, { status: 500 });
  }

  const token = request.cookies.get(COOKIE_NAME)?.value || "";
  if (!token || !verifyToken(token)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  return null;
}

async function proxyAdminRequest(
  request: NextRequest,
  params: { path: string[] },
): Promise<NextResponse> {
  const blocked = unauthorizedOrMisconfigured(request);
  if (blocked) return blocked;

  const path = params.path.join("/");
  const search = request.nextUrl.search || "";
  const headers: Record<string, string> = { "X-API-Key": SCRAPE_API_KEY };
  const init: RequestInit = {
    method: request.method,
    headers,
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.text();
    const contentType = request.headers.get("content-type");
    if (contentType) {
      headers["Content-Type"] = contentType;
    }
  }

  const resp = await fetch(`${API_INTERNAL_URL}/api/admin/${path}${search}`, init);
  const body = await resp.text();

  return new NextResponse(body || null, {
    status: resp.status,
    headers: {
      "Content-Type": resp.headers.get("content-type") || "application/json",
    },
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxyAdminRequest(request, params);
}

export async function POST(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxyAdminRequest(request, params);
}
