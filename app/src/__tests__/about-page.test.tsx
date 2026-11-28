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

it("charts full weeks only and labels the last one", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  const bars = screen.getAllByTestId("week-bar");
  // the current, unfinished week (23 Nov) is left out
  expect(bars).toHaveLength(2);
  expect(bars[1]).toHaveAttribute("title", "Week of 16 Nov: 1,234 new papers, 51 status updates");
  expect(within(bars[1]).getByText("1,234")).toBeInTheDocument();
  expect(screen.getByText(/last 2 full weeks/)).toBeInTheDocument();
  expect(screen.getByText("Show as table")).toBeInTheDocument();
});

it("leads with researchers tracked and new papers a week", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  const labels = screen.getAllByText(/^(Researchers tracked|New papers a week|Papers in the database|Top-5 articles found)$/);
  expect(labels.map((l) => l.textContent)).toEqual([
    "Researchers tracked",
    "New papers a week",
    "Papers in the database",
    "Top-5 articles found",
  ]);
});

it("explains a dip week, but not the ramp-up or the current week", async () => {
  const weeks = [
    { week_start: "2026-09-14", partial: false, new_papers: 3, status_changes: 0, events: 3 },
    ...[400, 380, 420, 46, 834].map((n, i) => ({
      week_start: ["2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"][i],
      partial: false, new_papers: n, status_changes: 0, events: n,
    })),
    { week_start: "2026-11-23", partial: true, new_papers: 10, status_changes: 0, events: 10 },
  ];
  mockFetch({ ok: true, status: 200, json: async () => ({ ...stats, activity: { ...stats.activity, weeks } }) });
  await renderAbout();
  expect(screen.getByText(/processing slowed \(week of 9 Nov\)/)).toBeInTheDocument();
});

it("adds no dip note in a normal period", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  expect(screen.queryByText(/processing slowed/)).not.toBeInTheDocument();
});

it("lists the share found per journal", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  expect(screen.getByRole("columnheader", { name: "Share found" })).toBeInTheDocument();
  const row = screen.getByRole("cell", { name: "American Economic Review" }).closest("tr")!;
  expect(within(row).getByText("72.2%")).toBeInTheDocument();
});

it("keeps How it works short and links to the opt-out information", async () => {
  mockFetch({ ok: true, status: 200, json: async () => stats });
  await renderAbout();
  expect(screen.getByRole("link", { name: "OpenAlex" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "corrections and removal" })).toHaveAttribute("href", "#contact");
  expect(screen.queryByText(/field tags/)).not.toBeInTheDocument();
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
