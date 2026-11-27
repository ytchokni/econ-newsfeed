/** @jest-environment node */
import { isValidElement, type ReactElement } from "react";

jest.mock("../app/researchers/[id]/ResearcherDetailContent", () => ({
  __esModule: true, default: () => null,
}));
jest.mock("../app/papers/[id]/PaperDetailContent", () => ({
  __esModule: true, default: () => null,
}));
jest.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

const researcher = {
  id: 7,
  first_name: "Ada",
  last_name: "Lovelace",
  position: "Professor",
  affiliation: "LSE",
  description: null,
  urls: [{ id: 1, page_type: "home", url: "https://ada.example.org/" }],
  website_url: "https://ada.example.org/",
  publication_count: 1,
  fields: [],
  jel_codes: [],
  openalex_author_id: "A1",
  publications: [{ id: 3, title: "Engines", authors: [] }],
};

const paper = {
  id: 3,
  title: "Engines",
  authors: [{ id: 7, first_name: "Ada", last_name: "Lovelace", affiliation: null }],
  year: "2025",
  venue: null,
  status: "working_paper",
  abstract: null,
  doi: null,
  openalex_id: null,
  feed_events: [],
  history: [],
};

function respond(status: number, body?: unknown) {
  global.fetch = jest.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body });
}

const params = (id: string) => ({ params: Promise.resolve({ id }) });

beforeEach(() => {
  jest.resetModules();
  delete process.env.INTERNAL_API_KEY;
});

function jsonLdOf(tree: unknown): Record<string, unknown> {
  // <> <JsonLd data=…/> <Content/> </>
  const children = (tree as ReactElement<{ children: ReactElement[] }>).props.children;
  const ld = children.find((c) => isValidElement(c) && (c.props as { data?: unknown }).data);
  return (ld as ReactElement<{ data: Record<string, unknown> }>).props.data;
}

describe("researcher page", () => {
  it("builds metadata and Person JSON-LD from the backend record", async () => {
    respond(200, researcher);
    const mod = await import("../app/researchers/[id]/page");
    const meta = await mod.generateMetadata(params("7"));
    expect(meta.title).toBe("Ada Lovelace — LSE");
    expect(meta.alternates?.canonical).toBe("/researchers/7");
    const ld = jsonLdOf(await mod.default(params("7")));
    expect(ld["@type"]).toBe("ProfilePage");
    expect((ld.mainEntity as Record<string, unknown>).name).toBe("Ada Lovelace");
  });

  it("returns a 404 for unknown or malformed ids", async () => {
    respond(404);
    const mod = await import("../app/researchers/[id]/page");
    await expect(mod.default(params("999"))).rejects.toThrow("NEXT_NOT_FOUND");
    await expect(mod.default(params("abc"))).rejects.toThrow("NEXT_NOT_FOUND");
    await expect(mod.default(params("007"))).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("errors (5xx, crawler retries) instead of a 404 when the backend fails", async () => {
    respond(429);
    const mod = await import("../app/researchers/[id]/page");
    await expect(mod.default(params("7"))).rejects.toThrow("Backend returned 429");
  });

  it("sends the internal API key on server-side fetches when configured", async () => {
    process.env.INTERNAL_API_KEY = "k".repeat(20);
    respond(200, researcher);
    const mod = await import("../app/researchers/[id]/page");
    await mod.default(params("7"));
    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/researchers/7",
      expect.objectContaining({ headers: { "X-Internal-Api-Key": "k".repeat(20) } }),
    );
  });
});

describe("paper page", () => {
  it("builds metadata and ScholarlyArticle JSON-LD", async () => {
    respond(200, paper);
    const mod = await import("../app/papers/[id]/page");
    const meta = await mod.generateMetadata(params("3"));
    expect(meta.title).toBe("Engines — Ada Lovelace");
    expect(meta.alternates?.canonical).toBe("/papers/3");
    const ld = jsonLdOf(await mod.default(params("3")));
    expect(ld["@type"]).toBe("ScholarlyArticle");
    expect(ld.author).toEqual([
      { "@type": "Person", name: "Ada Lovelace", url: "https://www.econnewsfeed.org/researchers/7" },
    ]);
  });

  it("returns a 404 for a missing paper", async () => {
    respond(404);
    const mod = await import("../app/papers/[id]/page");
    await expect(mod.default(params("3"))).rejects.toThrow("NEXT_NOT_FOUND");
    expect(await mod.generateMetadata(params("3"))).toEqual({});
  });
});
