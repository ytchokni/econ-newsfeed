import { NextRequest, NextResponse } from "next/server";
import { createHmac, timingSafeEqual } from "crypto";

const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const COOKIE_NAME = "admin_session";
const COOKIE_MAX_AGE = 7 * 24 * 60 * 60; // 7 days in seconds

function signToken(timestamp: number): string {
  return createHmac("sha256", ADMIN_PASSWORD)
    .update(String(timestamp))
    .digest("hex");
}

export async function POST(request: NextRequest) {
  if (!ADMIN_PASSWORD) {
    return NextResponse.json(
      { error: "Admin not configured" },
      { status: 500 }
    );
  }

  const body = await request.json().catch(() => null);
  const password = body?.password || "";

  if (typeof password !== "string" || password.length === 0) {
    return NextResponse.json({ error: "Password required" }, { status: 400 });
  }

  // Constant-time comparison
  const a = Buffer.from(password);
  const b = Buffer.from(ADMIN_PASSWORD);
  const valid =
    a.length === b.length && timingSafeEqual(a, b);

  if (!valid) {
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }

  const timestamp = Date.now();
  const token = `${timestamp}.${signToken(timestamp)}`;

  const response = NextResponse.json({ ok: true });
  response.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    maxAge: COOKIE_MAX_AGE,
    path: "/",
  });
  return response;
}
