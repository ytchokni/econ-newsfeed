/**
 * The home page pre-fills SWR with page 1 of the default feed. The server
 * HTML must contain those papers, and must not depend on the visitor's clock
 * or locale (React hydration error #418).
 */
import { renderToString } from "react-dom/server";
import { SWRConfig } from "swr";
import NewsfeedContent from "../NewsfeedContent";
import { defaultFeedUrl } from "@/lib/feed-params";
import type { PaginatedResponse, Publication } from "@/lib/types";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));

const now = new Date().toISOString();

const feed: PaginatedResponse<Publication> = {
  items: [
    {
      id: 1,
      title: "Immigration and Wages",
      authors: [{ id: 1, first_name: "Max", last_name: "Steinhardt", affiliation: null }],
      year: "2024",
      venue: "JLE",
      source_url: null,
      discovered_at: now,
      event_date: now,
      status: "published",
      abstract: null,
      draft_url: null,
      draft_url_status: "unchecked",
      draft_available: false,
      doi: null,
      coauthors: [],
      links: [],
    },
  ],
  total: 1234,
  page: 1,
  per_page: 20,
  pages: 62,
};

it("server-renders the pre-filled default feed with fixed date labels", () => {
  global.fetch = jest.fn(() => new Promise(() => {})) as unknown as typeof fetch;
  const html = renderToString(
    <SWRConfig value={{ provider: () => new Map(), fallback: { [defaultFeedUrl()]: feed } }}>
      <NewsfeedContent />
    </SWRConfig>,
  );
  expect(html).toContain("Immigration and Wages");
  // Absolute UTC day, not the clock-dependent "Today".
  expect(html).not.toContain("Today");
  const utcDay = new Date(now).toLocaleDateString("en-GB", {
    weekday: "long", day: "numeric", month: "long", timeZone: "UTC",
  });
  expect(html).toContain(utcDay);
  // Locale-independent number formatting.
  expect(html).toContain("1,234");
});
