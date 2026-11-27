// Feed URL state <-> API request. Pure (no React/SWR), so the server page can
// compute the exact SWR key the client will use and pre-fill it.

import type { FeedFilters } from "./types";

export type TabValue = "all" | "working_papers" | "publications" | "work_in_progress";

export const VALID_TABS: TabValue[] = ["all", "working_papers", "publications", "work_in_progress"];

export const FILTER_PARAM_KEYS = ["institution", "preset", "search", "jel_code", "since", "until", "researcher_id"] as const satisfies readonly (keyof Omit<FeedFilters, "event_type" | "status" | "year">)[];

export const TAB_FILTERS: Record<TabValue, { event_type?: string; status?: string }> = {
  all: {},
  working_papers: { event_type: "new_paper", status: "working_paper" },
  publications: { event_type: "status_change", status: "revise_and_resubmit,accepted,published" },
  work_in_progress: { event_type: "new_paper", status: "work_in_progress" },
};

export const FEED_PAGE_SIZE = 20;

export function parseTab(value: string | null): TabValue {
  if (value && VALID_TABS.includes(value as TabValue)) return value as TabValue;
  return "publications";
}

export function filtersFromParams(params: URLSearchParams): FeedFilters {
  const filters: FeedFilters = {};
  for (const key of FILTER_PARAM_KEYS) {
    const val = params.get(key);
    if (val) (filters as Record<string, string>)[key] = val;
  }
  // Explicit tabs or filters describe a shared view, including cleared filters.
  if (!params.has("tab") && Object.keys(filters).length === 0) {
    filters.preset = "top5_and_field";
  }
  return filters;
}

/** The user's filters plus the event type/status implied by the tab. */
export function mergeTabFilters(filters: FeedFilters, tab: TabValue): FeedFilters {
  const tabF = TAB_FILTERS[tab];
  return {
    ...filters,
    ...(tabF.event_type ? { event_type: tabF.event_type as FeedFilters["event_type"] } : {}),
    ...(tabF.status ? { status: tabF.status } : {}),
  };
}

export function buildPublicationsUrl(
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
  if (filters?.search) params.set("search", filters.search);
  if (filters?.event_type) params.set("event_type", filters.event_type);
  if (filters?.jel_code) params.set("jel_code", filters.jel_code);
  if (filters?.since) params.set("since", filters.since);
  if (filters?.until) params.set("until", filters.until);
  if (filters?.researcher_id) params.set("researcher_id", filters.researcher_id);
  return `/api/publications?${params.toString()}`;
}

/** The API URL (and SWR key) for the default feed a visitor without query parameters sees. */
export function defaultFeedUrl(): string {
  const params = new URLSearchParams();
  return buildPublicationsUrl(1, FEED_PAGE_SIZE, mergeTabFilters(filtersFromParams(params), parseTab(null)));
}
