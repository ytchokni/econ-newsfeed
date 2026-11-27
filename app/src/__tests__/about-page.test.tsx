import { render, screen, within } from "@testing-library/react";
import AboutPage, { metadata } from "../app/about/page";
import type { SiteStats } from "@/lib/site-stats";

const stats: SiteStats = {
  generated_at: "2026-11-27T23:59:00Z",
  researchers: { total: 166174, tracked: 41250, pages_tracked: 52310, with_fields: 9000 },
  papers: {
    total: 259794,
    by_status: { working_paper: 120000, under_review: 9000, accepted: 8000, published: 110000, unknown: 12794 },
  },
  activity: {
    weeks: [
      { week_start: "2026-11-09", partial: false, new_papers: 410, status_changes: 30, events: 445 },
      { week_start: "2026-11-16", partial: false, new_papers: 1234, status_changes: 51, events: 1290 },
      { week_start: "2026-11-23", partial: true, new_papers: 300, status_changes: 9, events: 309 },
    ],
    new_papers_per_week: 822,
    events_per_week: 868,
  },
  top5: {
    as_of: "2026-11-29T04:31:00Z",
    from_year: 2020,
    until_year: 2026,
    articles: 3011,
    found: 2131,
    coverage_pct: 70.8,
    tracked_articles: 2726,
    tracked_found: 2114,
    coverage_tracked_pct: 77.5,
    correct_record_pct: 68.3,
    by_journal: [
      { journal: "aer", name: "American Economic Review", articles: 900, found: 650, coverage_pct: 72.2, coverage_tracked_pct: 79.1 },
    ],
  },
  fetch_interval_hours: 48,
  last_fetch_at: "2026-11-26T06:00:00Z",
  last_extraction_at: "2026-11-27T23:58:00Z",
};

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  global.fetch = jest.fn().mockResolvedValue(response) as unknown as typeof fetch;
}

async function renderAbout() {
  render(await AboutPage());
}

it("fetches /api/stats on the server with revalidation", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  expect(global.fetch).toHaveBeenCalledWith(
    "http://localhost:8000/api/stats",
    expect.objectContaining({ next: { revalidate: 900 } }),
  );
});

it("shows the headline numbers", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  expect(screen.getByRole("heading", { level: 1, name: "About Econ Newsfeed" })).toBeInTheDocument();
  expect(screen.getByText("41,250")).toBeInTheDocument();
  expect(screen.getByText(/52,310 web pages; 166,174 researcher records/)).toBeInTheDocument();
  expect(screen.getByText("259,794")).toBeInTheDocument();
  expect(screen.getByText("822")).toBeInTheDocument();
  expect(screen.getAllByText("70.8%").length).toBeGreaterThan(0);
  expect(screen.getByText(/77\.5% where we track an author; since 2020/)).toBeInTheDocument();
  expect(screen.getByText(/every 2 days/)).toBeInTheDocument();
});

it("renders dates in UTC", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  // 23:59 UTC stays on the 27th whatever the test machine's time zone.
  expect(screen.getByText(/Figures as of 27 November 2026 \(UTC\)/)).toBeInTheDocument();
  expect(screen.getByText(/Last compared on 29 November 2026/)).toBeInTheDocument();
});

it("charts one bar per week and labels the last full week", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  const bars = screen.getAllByTestId("week-bar");
  expect(bars).toHaveLength(3);
  expect(bars[1]).toHaveAttribute("title", "Week of 16 Nov: 1,234 new papers, 51 status updates");
  expect(within(bars[1]).getByText("1,234")).toBeInTheDocument();
  expect(bars[2].getAttribute("title")).toMatch(/\(so far\)$/);
  // table view with the exact values
  expect(screen.getByText("Show as table")).toBeInTheDocument();
});

it("lists per-journal coverage", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  const row = screen.getByRole("cell", { name: "American Economic Review" }).closest("tr")!;
  expect(within(row).getByText("72.2%")).toBeInTheDocument();
  expect(within(row).getByText("79.1%")).toBeInTheDocument();
});

it("hides JEL field coverage while it is partial", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  expect(screen.queryByText(/field tags/)).not.toBeInTheDocument();
});

it("mentions JEL field coverage once most tracked researchers have it", async () => {
  const withFields = { ...stats, researchers: { ...stats.researchers, with_fields: 33000 } };
  mockFetch({ ok: true, status: 200, json: async () => withFields });
  await renderAbout();
  expect(screen.getByText(/80\.0% of tracked researchers also have field tags/)).toBeInTheDocument();
});

it("explains a missing top-5 comparison", async () => {
  mockFetch({ ok: true, status: 200, json: async () => ({ ...stats, top5: null }) });
  await renderAbout();
  expect(screen.getByText("First comparison pending")).toBeInTheDocument();
  expect(screen.getByText(/The first comparison has not run yet/)).toBeInTheDocument();
});

it("still renders the text when the backend fails", async () => {
  mockFetch({ ok: false, status: 503, json: async () => ({}) });
  await renderAbout();
  expect(screen.getByText(/live numbers are temporarily unavailable/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "How it works" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "privacy policy" })).toHaveAttribute("href", "/privacy");
  expect(screen.getByRole("link", { name: "Contact" })).toHaveAttribute("href", "/contact");
});

it("has its own canonical URL", () => {
  expect(metadata.alternates).toEqual({ canonical: "/about" });
  expect(metadata.title).toBe("About");
});
