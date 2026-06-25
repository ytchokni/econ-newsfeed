import useSWR from "swr";
import type {
  FeedFilters,
  FilterOptions,
  JelCode,
  NotificationPrefs,
  PaginatedResponse,
  Publication,
  PublicationDetail,
  ResearchField,
  Researcher,
  ResearcherDetail,
  ResearcherFilters,
  UserFollowing,
} from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

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
  if (filters?.search) params.set("search", filters.search);
  if (filters?.event_type) params.set("event_type", filters.event_type);
  if (filters?.jel_code) params.set("jel_code", filters.jel_code);
  if (filters?.since) params.set("since", filters.since);
  if (filters?.until) params.set("until", filters.until);
  return `/api/publications?${params.toString()}`;
}

export async function getPublications(
  page = 1,
  perPage = 20,
  filters?: FeedFilters
): Promise<PaginatedResponse<Publication>> {
  return fetchJson(buildPublicationsUrl(page, perPage, filters));
}

export async function getResearchers(): Promise<Researcher[]> {
  const data = await fetchJson<{ items: Researcher[] }>(
    `/api/researchers`
  );
  return data.items;
}

export async function getResearcher(id: number): Promise<ResearcherDetail> {
  return fetchJson(`/api/researchers/${id}`);
}

export function usePublications(page = 1, perPage = 20, filters?: FeedFilters) {
  const url = buildPublicationsUrl(page, perPage, filters);
  return useSWR<PaginatedResponse<Publication>>(url, fetchJson, {
    keepPreviousData: true,
  });
}

export function useResearchers() {
  return useSWR<Researcher[]>("/api/researchers", async (url: string) => {
    const data = await fetchJson<{ items: Researcher[] }>(url);
    return data.items;
  });
}

export function useResearcher(id: number, fallbackData?: ResearcherDetail) {
  return useSWR<ResearcherDetail>(`/api/researchers/${id}`, fetchJson, {
    fallbackData,
  });
}

export async function getFields(): Promise<ResearchField[]> {
  const data = await fetchJson<{ items: ResearchField[] }>(
    `/api/fields`
  );
  return data.items;
}

export function useFilterOptions() {
  return useSWR<FilterOptions>("/api/filter-options", fetchJson);
}

export function useJelCodes() {
  return useSWR<JelCode[]>("/api/jel-codes", async (url: string) => {
    const data = await fetchJson<{ items: JelCode[] }>(url);
    return data.items;
  });
}

export function usePublication(id: number, fallbackData?: PublicationDetail) {
  return useSWR<PublicationDetail>(
    `/api/publications/${id}?include_history=true`,
    fetchJson,
    { fallbackData }
  );
}

export function useResearchersFiltered(filters?: ResearcherFilters) {
  const params = new URLSearchParams({ per_page: "100" });
  if (filters?.institution) params.set("institution", filters.institution);
  if (filters?.field) params.set("field", filters.field);
  if (filters?.position) params.set("position", filters.position);
  if (filters?.search) params.set("search", filters.search);
  if (filters?.preset) params.set("preset", filters.preset);
  const url = `/api/researchers?${params.toString()}`;
  return useSWR<Researcher[]>(url, async (u: string) => {
    const data = await fetchJson<{ items: Researcher[]; total: number }>(u);
    return data.items;
  });
}

export interface AdminDashboardData {
  health: {
    last_scrape: {
      started_at: string;
      status: string;
      urls_checked: number;
      urls_changed: number;
      pubs_extracted: number;
      duration_seconds: number | null;
    } | null;
    next_scrape_at: string | null;
    scrape_in_progress: boolean;
    total_researcher_urls: number;
    urls_by_page_type: Record<string, number>;
    deactivated_urls: number;
    at_risk_urls: number;
  };
  content: {
    total_papers: number;
    total_researchers: number;
    papers_by_status: Record<string, number>;
    papers_by_year: { year: string; count: number }[];
    researchers_by_position: Record<string, number>;
  };
  quality: {
    papers_with_abstract: number;
    papers_with_doi: number;
    papers_with_openalex: number;
    papers_with_draft_url: number;
    draft_url_valid: number;
    researchers_with_description: number;
    researchers_with_jel: number;
    researchers_with_openalex_id: number;
  };
  costs: {
    total_cost_usd: number;
    total_tokens: number;
    by_call_type: {
      call_type: string;
      cost: number;
      tokens: number;
      count: number;
    }[];
    by_model: { model: string; cost: number; tokens: number }[];
    batch_vs_realtime: { batch_cost: number; realtime_cost: number };
    last_30_days: { date: string; cost: number; tokens: number }[];
  };
  scrapes: {
    recent: {
      started_at: string;
      status: string;
      urls_checked: number;
      urls_changed: number;
      pubs_extracted: number;
      tokens_used: number;
      duration_seconds: number | null;
    }[];
    totals: { total_scrapes: number; total_pubs_extracted: number };
  };
  activity: {
    events_last_7d: Record<string, number>;
    events_last_30d: Record<string, number>;
    recent_events: {
      event_type: string;
      paper_title: string;
      created_at: string;
      details: string | null;
    }[];
  };
  extraction: {
    worker_enabled: boolean;
    queue: { never_extracted: number; changed_pending: number; total: number };
    throughput: {
      completions: { last_hour: number; last_24h: number; last_7d: number };
      attempts: { last_hour: number; last_24h: number; last_7d: number };
    };
    eta_days: number | null;
    last_call_at: string | null;
    last_extracted_at: string | null;
    tokens_last_24h: number;
    daily: { date: string; count: number }[];
    recent_calls: {
      called_at: string;
      context_url: string | null;
      model: string;
      total_tokens: number;
    }[];
  };
  discovery: {
    total_searched: number;
    pending_review: number;
    approved: number;
    rejected: number;
    no_result: number;
    pool_remaining: number;
  };
}

async function fetchJsonWithAuth<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (res.status === 401) {
    throw new Error("UNAUTHORIZED");
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function useAdminDashboard() {
  return useSWR<AdminDashboardData>(
    "/api/admin/dashboard",
    fetchJsonWithAuth,
    { refreshInterval: 60000 }
  );
}

export interface DeactivatedUrl {
  id: number;
  url: string;
  page_type: string;
  deactivation_reason: string;
  deactivated_at: string;
  consecutive_failures: number;
  researcher_name: string;
  researcher_id: number;
}

export interface AtRiskUrl {
  id: number;
  url: string;
  page_type: string;
  consecutive_failures: number;
  researcher_name: string;
  researcher_id: number;
}

export interface UrlDiscovery {
  id: number;
  researcher_id: number;
  url: string | null;
  subpages: { page_type: string; url: string }[] | null;
  confidence: number | null;
  search_query: string;
  searched_at: string;
  reviewed_at: string | null;
  first_name: string;
  last_name: string;
  affiliation: string | null;
  status?: string;
}

export interface DiscoveriesResponse {
  pending: UrlDiscovery[];
  recent: UrlDiscovery[];
}

export function useDeactivatedUrls() {
  return useSWR<DeactivatedUrl[]>(
    "/api/admin/deactivated-urls",
    fetchJsonWithAuth,
  );
}

export function useAtRiskUrls() {
  return useSWR<AtRiskUrl[]>(
    "/api/admin/at-risk-urls",
    fetchJsonWithAuth,
  );
}

export async function reactivateUrl(urlId: number): Promise<void> {
  await fetchJsonWithAuth(`/api/admin/reactivate-url/${urlId}`, {
    method: "POST",
  });
}

export function useDiscoveries() {
  return useSWR<DiscoveriesResponse>(
    "/api/admin/discoveries",
    fetchJsonWithAuth,
  );
}

export async function approveDiscovery(discoveryId: number): Promise<void> {
  await fetchJsonWithAuth(`/api/admin/discoveries/${discoveryId}/approve`, {
    method: "POST",
  });
}

export async function rejectDiscovery(discoveryId: number): Promise<void> {
  await fetchJsonWithAuth(`/api/admin/discoveries/${discoveryId}/reject`, {
    method: "POST",
  });
}

export async function bulkApproveDiscoveries(): Promise<{ approved_count: number }> {
  return fetchJsonWithAuth("/api/admin/discoveries/bulk-approve", {
    method: "POST",
  });
}

// --- Authenticated API helpers ---

async function fetchJsonAuth<T>(url: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${token}`,
    },
  });
  if (res.status === 401) throw new Error("UNAUTHORIZED");
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

export const followingSwrKey = (token: string | null) =>
  token ? ["/api/users/following", token] as const : null;

export function useFollowing(token: string | null) {
  return useSWR<UserFollowing>(
    followingSwrKey(token),
    ([url, t]: [string, string]) => fetchJsonAuth(url, t),
  );
}

export async function followResearcher(researcherId: number, token: string): Promise<void> {
  await fetchJsonAuth(`/api/users/follow/${researcherId}`, token, { method: "POST" });
}

export async function unfollowResearcher(researcherId: number, token: string): Promise<void> {
  await fetchJsonAuth(`/api/users/follow/${researcherId}`, token, { method: "DELETE" });
}

export function useNotificationPrefs(token: string | null) {
  return useSWR<NotificationPrefs>(
    token ? ["/api/users/notifications", token] : null,
    ([url, t]: [string, string]) => fetchJsonAuth(url, t),
  );
}

export async function updateNotificationPrefs(
  prefs: { digest_enabled: boolean },
  token: string,
): Promise<void> {
  await fetchJsonAuth("/api/users/notifications", token, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(prefs),
  });
}
