import useSWR from "swr";
import type {
  FeedFilters,
  FilterOptions,
  JelCode,
  PaginatedResponse,
  Publication,
  PublicationDetail,
  ResearchField,
  Researcher,
  ResearcherDetail,
  ResearcherFilters,
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
  const url = `/api/researchers?${params.toString()}`;
  return useSWR<Researcher[]>(url, async (u: string) => {
    const data = await fetchJson<{ items: Researcher[] }>(u);
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
}

async function fetchJsonWithAuth<T>(url: string): Promise<T> {
  const res = await fetch(url);
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
