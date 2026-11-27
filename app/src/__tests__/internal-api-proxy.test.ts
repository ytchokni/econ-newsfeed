/** @jest-environment node */
import { NextRequest } from "next/server";

jest.mock("next/headers", () => ({
  headers: jest.fn(async () => new Headers({ "x-real-ip": "198.51.100.77" })),
}));
jest.mock("../app/researchers/[id]/ResearcherDetailContent", () => ({
  __esModule: true, default: () => null,
}));
jest.mock("../app/papers/[id]/PaperDetailContent", () => ({
  __esModule: true, default: () => null,
}));

const KEY = "test-internal-key";
const originalKey = process.env.INTERNAL_API_KEY;

function apiRequest(headers: Record<string, string> = {}) {
  return new NextRequest("https://www.econnewsfeed.org/api/publications?page=1", { headers });
}

function forwarded(response: Response, name: string): string | null {
  // NextResponse.next({ request: { headers } }) encodes upstream request headers this way.
  return response.headers.get(`x-middleware-request-${name}`);
}

beforeEach(() => {
  jest.resetModules();
  jest.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  if (originalKey === undefined) delete process.env.INTERNAL_API_KEY;
  else process.env.INTERNAL_API_KEY = originalKey;
  jest.restoreAllMocks();
});

describe("proxy", () => {
  it("adds the internal key and the Vercel-reported visitor IP to proxied API calls", async () => {
    process.env.INTERNAL_API_KEY = KEY;
    const { proxy } = await import("../proxy");
    const res = proxy(apiRequest({
      "x-real-ip": "203.0.113.5",
      "x-forwarded-for": "203.0.113.5",
      "user-agent": "Mozilla/5.0",
    }));
    expect(forwarded(res, "x-internal-api-key")).toBe(KEY);
    expect(forwarded(res, "x-visitor-ip")).toBe("203.0.113.5");
    expect(forwarded(res, "x-visitor-source")).toBe("proxy");
    // Other request headers still travel with the rewrite.
    expect(forwarded(res, "user-agent")).toBe("Mozilla/5.0");
    expect(res.headers.get("x-middleware-override-headers")).toContain("x-internal-api-key");
  });

  it("overwrites visitor headers a client tried to supply", async () => {
    process.env.INTERNAL_API_KEY = KEY;
    const { proxy } = await import("../proxy");
    const res = proxy(apiRequest({
      "x-real-ip": "203.0.113.5",
      "x-visitor-ip": "6.6.6.6",
      "x-visitor-source": "ssr",
      "x-internal-api-key": "guess",
    }));
    expect(forwarded(res, "x-visitor-ip")).toBe("203.0.113.5");
    expect(forwarded(res, "x-visitor-source")).toBe("proxy");
    expect(forwarded(res, "x-internal-api-key")).toBe(KEY);
  });

  it("passes requests through unchanged when INTERNAL_API_KEY is unset", async () => {
    delete process.env.INTERNAL_API_KEY;
    const { proxy } = await import("../proxy");
    const res = proxy(apiRequest({ "x-real-ip": "203.0.113.5" }));
    expect(res.headers.get("x-middleware-next")).toBe("1");
    expect(res.headers.get("x-middleware-override-headers")).toBeNull();
    expect(forwarded(res, "x-internal-api-key")).toBeNull();
    expect(console.warn).toHaveBeenCalledTimes(1);
  });

  it("matches the same paths as the /api rewrite", async () => {
    const { readFileSync } = await import("fs");
    const { join } = await import("path");
    const { config } = await import("../proxy");
    const nextConfig = readFileSync(join(__dirname, "../../next.config.mjs"), "utf-8");
    expect(nextConfig).toContain(`source: "${config.matcher}"`);
  });
});

describe("visitorIp", () => {
  it("prefers x-real-ip, then the first x-forwarded-for entry", async () => {
    const { visitorIp } = await import("../lib/internal-api");
    expect(visitorIp(new Headers({ "x-real-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2" }))).toBe("1.1.1.1");
    expect(visitorIp(new Headers({ "x-forwarded-for": "2.2.2.2, 3.3.3.3" }))).toBe("2.2.2.2");
    expect(visitorIp(new Headers())).toBe("unknown");
  });
});

describe("server-rendered detail pages", () => {
  beforeEach(() => {
    delete process.env.API_INTERNAL_URL;
    global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: 42 }) });
  });

  it("send the internal key and the visitor IP with SSR fetches", async () => {
    process.env.INTERNAL_API_KEY = KEY;
    const { default: Page } = await import("../app/researchers/[id]/page");
    await Page({ params: Promise.resolve({ id: "42" }) });
    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/researchers/42",
      expect.objectContaining({
        headers: { "x-internal-api-key": KEY, "x-visitor-ip": "198.51.100.77", "x-visitor-source": "ssr" },
      }),
    );
  });

  it("send no extra headers and skip request headers without INTERNAL_API_KEY", async () => {
    delete process.env.INTERNAL_API_KEY;
    const nextHeaders = await import("next/headers");
    const { default: Page } = await import("../app/papers/[id]/page");
    await Page({ params: Promise.resolve({ id: "42" }) });
    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/publications/42?include_history=true",
      expect.objectContaining({ headers: {} }),
    );
    expect(nextHeaders.headers).not.toHaveBeenCalled();
  });

  it("fail instead of returning 404 when the backend rate-limits the render", async () => {
    process.env.INTERNAL_API_KEY = KEY;
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 429, json: async () => ({}) });
    const { default: Page } = await import("../app/papers/[id]/page");
    await expect(Page({ params: Promise.resolve({ id: "42" }) })).rejects.toThrow("rate limit");
  });
});
