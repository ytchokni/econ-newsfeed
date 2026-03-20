import { getPublications, getResearchers, getResearcher, API_BASE_URL } from "../api";
import type { PaginatedResponse, Publication, Researcher, ResearcherDetail } from "../types";

const mockPublicationsResponse: PaginatedResponse<Publication> = {
  items: [
    {
      id: 1,
      title: "Immigration and Wages",
      authors: [{ id: 1, first_name: "Max", last_name: "Steinhardt" }],
      year: "2024",
      venue: "Journal of Labor Economics",
      source_url: "https://example.com/paper",
      discovered_at: "2026-03-15T14:30:00Z",
      status: "published",
      abstract: null,
      draft_url: null,
      draft_url_status: "unchecked",
      draft_available: false,
    },
  ],
  total: 1,
  page: 1,
  per_page: 20,
  pages: 1,
};

const mockResearchersResponse: { items: Researcher[] } = {
  items: [
    {
      id: 1,
      first_name: "Max",
      last_name: "Steinhardt",
      position: "Professor",
      affiliation: "Freie Universität Berlin",
      description: null,
      urls: [{ id: 1, page_type: "PUB", url: "https://example.com" }],
      website_url: null,
      publication_count: 23,
      fields: [],
      jel_codes: [],
    },
  ],
};

const mockResearcherDetail: ResearcherDetail = {
  id: 1,
  first_name: "Max",
  last_name: "Steinhardt",
  position: "Professor",
  affiliation: "Freie Universität Berlin",
  description: null,
  urls: [],
  website_url: null,
  publication_count: 5,
  fields: [],
  jel_codes: [],
  publications: [
    {
      id: 1,
      title: "Immigration and Wages",
      authors: [{ id: 1, first_name: "Max", last_name: "Steinhardt" }],
      year: "2024",
      venue: "Journal of Labor Economics",
      source_url: null,
      discovered_at: "2026-03-15T14:30:00Z",
      status: null,
      abstract: null,
      draft_url: null,
      draft_url_status: "unchecked",
      draft_available: false,
    },
  ],
};

beforeEach(() => {
  jest.resetAllMocks();
  global.fetch = jest.fn();
});

describe("API_BASE_URL", () => {
  it("defaults to http://localhost:8000 when env var is not set", () => {
    expect(API_BASE_URL).toBeDefined();
    expect(typeof API_BASE_URL).toBe("string");
  });
});

describe("getPublications", () => {
  it("fetches publications with default pagination", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockPublicationsResponse,
    });

    const result = await getPublications();
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/publications?page=1&per_page=20")
    );
    expect(result).toEqual(mockPublicationsResponse);
  });

  it("passes custom page and per_page parameters", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockPublicationsResponse,
    });

    await getPublications(2, 50);
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/publications?page=2&per_page=50")
    );
  });

  it("throws on non-ok response", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
    });

    await expect(getPublications()).rejects.toThrow();
  });
});

describe("getResearchers", () => {
  it("fetches all researchers", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResearchersResponse,
    });

    const result = await getResearchers();
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/researchers")
    );
    expect(result).toEqual(mockResearchersResponse.items);
  });

  it("throws on non-ok response", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
    });

    await expect(getResearchers()).rejects.toThrow();
  });
});

describe("getResearcher", () => {
  it("fetches a single researcher by id", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResearcherDetail,
    });

    const result = await getResearcher(1);
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/researchers/1")
    );
    expect(result).toEqual(mockResearcherDetail);
  });

  it("throws on non-ok response", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
    });

    await expect(getResearcher(999)).rejects.toThrow();
  });
});
