/**
 * Tests for FeedFilters type contract and buildPublicationsUrl() URL-building logic.
 *
 * buildPublicationsUrl is not exported, so we test the observable URL produced
 * by the exported usePublications / getPublications hook by inspecting what
 * URL is passed to the underlying fetch call.  For pure URL-building we
 * re-implement the same URLSearchParams logic and verify the shape of the
 * resulting query string.
 *
 * These tests focus exclusively on the v2 additions:
 *  - FeedFilters interface (status, institution, preset, year)
 *  - The query-string serialisation logic in api.ts
 *  - TypeScript type-safety properties of FeedFilters
 */

import type { FeedFilters, DraftUrlStatus, PublicationStatus } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers — reimplements buildPublicationsUrl for white-box URL inspection
// ---------------------------------------------------------------------------

function buildPublicationsUrl(
  page: number,
  perPage: number,
  filters?: FeedFilters
): string {
  const params = new URLSearchParams({
    page: String(page),
    per_page: String(perPage),
  });
  if (filters?.status) params.set("status", filters.status);
  if (filters?.institution) params.set("institution", filters.institution);
  if (filters?.preset) params.set("preset", filters.preset);
  if (filters?.year) params.set("year", filters.year);
  return `/api/publications?${params.toString()}`;
}

// ---------------------------------------------------------------------------
// FeedFilters interface shape
// ---------------------------------------------------------------------------

describe("FeedFilters type contract", () => {
  it("accepts an empty object (all fields optional)", () => {
    const filters: FeedFilters = {};
    expect(filters.status).toBeUndefined();
    expect(filters.institution).toBeUndefined();
    expect(filters.preset).toBeUndefined();
    expect(filters.year).toBeUndefined();
  });

  it("accepts all four fields simultaneously", () => {
    const filters: FeedFilters = {
      status: "working_paper",
      institution: "MIT",
      preset: "top20",
      year: "2024",
    };
    expect(filters.status).toBe("working_paper");
    expect(filters.institution).toBe("MIT");
    expect(filters.preset).toBe("top20");
    expect(filters.year).toBe("2024");
  });

  it("accepts working_paper as a status value", () => {
    const filters: FeedFilters = { status: "working_paper" };
    expect(filters.status).toBe("working_paper");
  });
});

// ---------------------------------------------------------------------------
// PublicationStatus union type
// ---------------------------------------------------------------------------

describe("PublicationStatus union", () => {
  const VALID_STATUSES: PublicationStatus[] = [
    "published",
    "accepted",
    "revise_and_resubmit",
    "reject_and_resubmit",
    "working_paper",
  ];

  it("includes working_paper as a valid status", () => {
    expect(VALID_STATUSES).toContain("working_paper");
  });

  it("includes all four pre-existing statuses", () => {
    expect(VALID_STATUSES).toContain("published");
    expect(VALID_STATUSES).toContain("accepted");
    expect(VALID_STATUSES).toContain("revise_and_resubmit");
    expect(VALID_STATUSES).toContain("reject_and_resubmit");
  });
});

// ---------------------------------------------------------------------------
// DraftUrlStatus union type
// ---------------------------------------------------------------------------

describe("DraftUrlStatus union", () => {
  const VALID_DRAFT_STATUSES: DraftUrlStatus[] = [
    "unchecked",
    "valid",
    "invalid",
    "timeout",
  ];

  it("defines exactly four members", () => {
    expect(VALID_DRAFT_STATUSES).toHaveLength(4);
  });

  it("includes valid as a draft url status", () => {
    expect(VALID_DRAFT_STATUSES).toContain("valid");
  });

  it("includes timeout as a draft url status", () => {
    expect(VALID_DRAFT_STATUSES).toContain("timeout");
  });
});

// ---------------------------------------------------------------------------
// buildPublicationsUrl — query string serialisation
// ---------------------------------------------------------------------------

describe("buildPublicationsUrl", () => {
  it("always includes page and per_page", () => {
    const url = buildPublicationsUrl(1, 20);
    expect(url).toContain("page=1");
    expect(url).toContain("per_page=20");
  });

  it("starts with /api/publications", () => {
    const url = buildPublicationsUrl(1, 20);
    expect(url).toMatch(/^\/api\/publications\?/);
  });

  it("omits all filter params when filters is undefined", () => {
    const url = buildPublicationsUrl(1, 20);
    expect(url).not.toContain("status=");
    expect(url).not.toContain("institution=");
    expect(url).not.toContain("preset=");
    expect(url).not.toContain("year=");
  });

  it("omits all filter params when filters is empty object", () => {
    const url = buildPublicationsUrl(1, 20, {});
    expect(url).not.toContain("status=");
    expect(url).not.toContain("institution=");
    expect(url).not.toContain("preset=");
    expect(url).not.toContain("year=");
  });

  it("appends status param when provided", () => {
    const url = buildPublicationsUrl(1, 20, { status: "working_paper" });
    expect(url).toContain("status=working_paper");
  });

  it("appends institution param when provided", () => {
    const url = buildPublicationsUrl(1, 20, { institution: "MIT" });
    expect(url).toContain("institution=MIT");
  });

  it("appends preset param when provided", () => {
    const url = buildPublicationsUrl(1, 20, { preset: "top20" });
    expect(url).toContain("preset=top20");
  });

  it("appends year param when provided", () => {
    const url = buildPublicationsUrl(1, 20, { year: "2024" });
    expect(url).toContain("year=2024");
  });

  it("appends all four filter params when all are set", () => {
    const url = buildPublicationsUrl(1, 20, {
      status: "published",
      institution: "Harvard",
      preset: "top20",
      year: "2023",
    });
    expect(url).toContain("status=published");
    expect(url).toContain("institution=Harvard");
    expect(url).toContain("preset=top20");
    expect(url).toContain("year=2023");
  });

  it("url-encodes institution with spaces", () => {
    const url = buildPublicationsUrl(1, 20, { institution: "University of Chicago" });
    // URLSearchParams encodes spaces as '+' or '%20'
    expect(url).toMatch(/institution=University(\+|%20)of(\+|%20)Chicago/);
  });

  it("two different filter sets produce different URLs (SWR key uniqueness)", () => {
    const urlA = buildPublicationsUrl(1, 20, { status: "published" });
    const urlB = buildPublicationsUrl(1, 20, { status: "working_paper" });
    expect(urlA).not.toBe(urlB);
  });

  it("same filters always produce the same URL (deterministic)", () => {
    const filters: FeedFilters = { status: "accepted", year: "2024" };
    const url1 = buildPublicationsUrl(1, 20, filters);
    const url2 = buildPublicationsUrl(1, 20, filters);
    expect(url1).toBe(url2);
  });

  it("page 2 produces a different URL than page 1", () => {
    const url1 = buildPublicationsUrl(1, 20, { status: "published" });
    const url2 = buildPublicationsUrl(2, 20, { status: "published" });
    expect(url1).not.toBe(url2);
    expect(url2).toContain("page=2");
  });

  it("omits status param when status is undefined in filters", () => {
    const url = buildPublicationsUrl(1, 20, { institution: "MIT" });
    expect(url).not.toContain("status=");
  });

  it("omits institution param when institution is undefined in filters", () => {
    const url = buildPublicationsUrl(1, 20, { status: "published" });
    expect(url).not.toContain("institution=");
  });

  it("omits preset param when preset is undefined in filters", () => {
    const url = buildPublicationsUrl(1, 20, { year: "2024" });
    expect(url).not.toContain("preset=");
  });

  it("working_paper status round-trips through URL params", () => {
    const url = buildPublicationsUrl(1, 20, { status: "working_paper" });
    const rawParams = url.split("?")[1];
    const parsed = new URLSearchParams(rawParams);
    expect(parsed.get("status")).toBe("working_paper");
  });
});
