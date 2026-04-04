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

  // Check expiry
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

export async function GET(request: NextRequest) {
  if (!ADMIN_PASSWORD) {
    return NextResponse.json({ error: "Not configured" }, { status: 500 });
  }

  const token = request.cookies.get(COOKIE_NAME)?.value || "";
  if (!token || !verifyToken(token)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const resp = await fetch(`${API_INTERNAL_URL}/api/admin/dashboard`, {
    headers: { "X-API-Key": SCRAPE_API_KEY },
  });

  if (!resp.ok) {
    return NextResponse.json(
      { error: "Backend error" },
      { status: resp.status }
    );
  }

  const data = await resp.json();
  return NextResponse.json(data);
}
