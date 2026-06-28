/**
 * @jest-environment node
 */
import { createHmac } from "crypto";
import type { NextRequest } from "next/server";

const fetchMock = jest.fn();

function signAdminToken(password: string, timestamp = Date.now()): string {
  const signature = createHmac("sha256", password)
    .update(String(timestamp))
    .digest("hex");
  return `${timestamp}.${signature}`;
}

function makeRequest({
  method,
  token,
  search = "",
  body = "",
  contentType,
}: {
  method: string;
  token?: string;
  search?: string;
  body?: string;
  contentType?: string;
}): NextRequest {
  return {
    method,
    cookies: {
      get: (name: string) =>
        name === "admin_session" && token ? { value: token } : undefined,
    },
    nextUrl: { search },
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "content-type" ? contentType || null : null,
    },
    text: jest.fn(async () => body),
  } as unknown as NextRequest;
}

async function loadRoute() {
  jest.resetModules();
  return import("../app/api/admin/[...path]/route");
}

describe("admin API proxy route", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    global.fetch = fetchMock as unknown as typeof fetch;
    process.env.ADMIN_PASSWORD = "correct horse battery staple";
    process.env.SCRAPE_API_KEY = "scrape-secret-for-tests";
    process.env.API_INTERNAL_URL = "http://backend.test";
  });

  it("adds the scrape API key for authenticated admin GET requests", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ pending: [], recent: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { GET } = await loadRoute();
    const request = makeRequest({
      method: "GET",
      token: signAdminToken(process.env.ADMIN_PASSWORD!),
      search: "?limit=20",
    });

    const response = await GET(request, {
      params: { path: ["discoveries"] },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://backend.test/api/admin/discoveries?limit=20",
      expect.objectContaining({
        method: "GET",
        headers: { "X-API-Key": "scrape-secret-for-tests" },
      }),
    );
    await expect(response.json()).resolves.toEqual({ pending: [], recent: [] });
  });

  it("forwards POST bodies while injecting the scrape API key", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { POST } = await loadRoute();
    const request = makeRequest({
      method: "POST",
      token: signAdminToken(process.env.ADMIN_PASSWORD!),
      body: JSON.stringify({ reason: "duplicate" }),
      contentType: "application/json",
    });

    const response = await POST(request, {
      params: { path: ["discoveries", "123", "reject"] },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://backend.test/api/admin/discoveries/123/reject",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reason: "duplicate" }),
        headers: {
          "X-API-Key": "scrape-secret-for-tests",
          "Content-Type": "application/json",
        },
      }),
    );
    await expect(response.json()).resolves.toEqual({ status: "ok" });
  });

  it("rejects requests without a valid admin session before proxying", async () => {
    const { GET } = await loadRoute();
    const request = makeRequest({ method: "GET" });

    const response = await GET(request, {
      params: { path: ["discoveries"] },
    });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
