/** @jest-environment node */
import {
  buildSitemapIndex,
  buildUrlset,
  chunkEntries,
  parseSitemapFile,
  sitemapFiles,
} from "@/lib/sitemap";

const ORIGIN = "https://www.econnewsfeed.org";

function mockFetch(responses: Record<string, { status: number; body?: unknown } | Error>) {
  global.fetch = jest.fn(async (url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    const r = responses[path];
    if (!r) throw new Error(`unexpected fetch ${path}`);
    if (r instanceof Error) throw r;
    return { ok: r.status < 400, status: r.status, json: async () => r.body } as Response;
  }) as unknown as typeof fetch;
}

describe("sitemap helpers", () => {
  it("names one file per backend chunk plus the static pages", () => {
    expect(
      sitemapFiles({ page_size: 10000, researchers: { count: 11042, pages: 2 }, papers: { count: 5, pages: 1 } }),
    ).toEqual(["pages.xml", "researchers-0.xml", "researchers-1.xml", "papers-0.xml"]);
  });

  it("parses only known file names", () => {
    expect(parseSitemapFile("pages.xml")).toEqual({ kind: "pages" });
    expect(parseSitemapFile("papers-12.xml")).toEqual({ kind: "papers", page: 12 });
    expect(parseSitemapFile("papers-999.xml")).toEqual({ kind: "papers", page: 999 });
    expect(parseSitemapFile("researchers-0.xml")).toEqual({ kind: "researchers", page: 0 });
    for (const bad of ["papers-01.xml", "papers--1.xml", "users-0.xml", "papers-0.txt", "papers-1000.xml", "papers-99999.xml"]) {
      expect(parseSitemapFile(bad)).toBeNull();
    }
  });

  it("builds a valid urlset with escaped locations and lastmod", () => {
    const xml = buildUrlset([
      { loc: `${ORIGIN}/a?b=1&c=<2>`, lastmod: "2026-06-10T07:42:05Z" },
      { loc: `${ORIGIN}/researchers` },
    ]);
    expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true);
    expect(xml).toContain('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">');
    expect(xml).toContain(`<url><loc>${ORIGIN}/a?b=1&amp;c=&lt;2&gt;</loc><lastmod>2026-06-10T07:42:05Z</lastmod></url>`);
    expect(xml).toContain(`<url><loc>${ORIGIN}/researchers</loc></url>`);
  });

  it("builds a sitemap index", () => {
    const xml = buildSitemapIndex([{ loc: `${ORIGIN}/sitemaps/pages.xml` }]);
    expect(xml).toContain('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">');
    expect(xml).toContain(`<sitemap><loc>${ORIGIN}/sitemaps/pages.xml</loc></sitemap>`);
  });

  it("turns backend chunks into canonical page URLs", () => {
    expect(chunkEntries("papers", { items: [{ id: 7, lastmod: null }] }, ORIGIN)).toEqual([
      { loc: `${ORIGIN}/papers/7`, lastmod: null },
    ]);
  });
});

describe("sitemap routes", () => {
  const originalSiteUrl = process.env.SITE_URL;
  beforeEach(() => {
    jest.resetModules();
    delete process.env.SITE_URL;
  });
  afterAll(() => {
    if (originalSiteUrl === undefined) delete process.env.SITE_URL;
    else process.env.SITE_URL = originalSiteUrl;
  });

  it("/sitemap.xml lists every chunk on the canonical host", async () => {
    mockFetch({
      "/api/sitemap": {
        status: 200,
        body: { page_size: 10000, researchers: { count: 3, pages: 1 }, papers: { count: 20001, pages: 3 } },
      },
    });
    const { GET } = await import("../app/sitemap.xml/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/xml");
    const xml = await res.text();
    expect(xml.match(/<sitemap>/g)).toHaveLength(5);
    expect(xml).toContain(`${ORIGIN}/sitemaps/papers-2.xml`);
  });

  it("/sitemap.xml answers 503 (retry later) when the backend fails", async () => {
    mockFetch({ "/api/sitemap": { status: 500 } });
    const { GET } = await import("../app/sitemap.xml/route");
    const res = await GET();
    expect(res.status).toBe(503);
    expect(res.headers.get("retry-after")).toBe("3600");
  });

  async function getFile(file: string) {
    const { GET } = await import("../app/sitemaps/[file]/route");
    return GET(new Request(`http://localhost/sitemaps/${file}`), { params: Promise.resolve({ file }) });
  }

  it("serves a researcher chunk with lastmod", async () => {
    mockFetch({
      "/api/sitemap/researchers?page=0": {
        status: 200,
        body: { items: [{ id: 1, lastmod: "2026-06-10T07:42:05Z" }, { id: 2, lastmod: null }] },
      },
    });
    const res = await getFile("researchers-0.xml");
    expect(res.status).toBe(200);
    const xml = await res.text();
    expect(xml).toContain(`<url><loc>${ORIGIN}/researchers/1</loc><lastmod>2026-06-10T07:42:05Z</lastmod></url>`);
    expect(xml).toContain(`<url><loc>${ORIGIN}/researchers/2</loc></url>`);
  });

  it("serves the static pages without calling the backend", async () => {
    mockFetch({});
    const res = await getFile("pages.xml");
    const xml = await res.text();
    expect(xml).toContain(`<loc>${ORIGIN}/</loc>`);
    expect(xml).toContain(`<loc>${ORIGIN}/researchers</loc>`);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("404s when the backend rejects or does not have the page", async () => {
    mockFetch({
      "/api/sitemap/papers?page=5": { status: 404 },
      "/api/sitemap/papers?page=6": { status: 422 },
      "/api/sitemap/papers?page=7": { status: 429 },
    });
    expect((await getFile("papers-5.xml")).status).toBe(404);
    expect((await getFile("papers-6.xml")).status).toBe(404);
    // Rate limiting is temporary: crawlers should retry.
    expect((await getFile("papers-7.xml")).status).toBe(503);
  });

  it("404s for unknown files and empty chunks, 503s on backend errors", async () => {
    mockFetch({
      "/api/sitemap/papers?page=9": { status: 200, body: { items: [] } },
      "/api/sitemap/papers?page=1": new Error("connection refused"),
    });
    expect((await getFile("users-0.xml")).status).toBe(404);
    expect((await getFile("papers-9.xml")).status).toBe(404);
    expect((await getFile("papers-1.xml")).status).toBe(503);
  });
});
