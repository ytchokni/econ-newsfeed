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

export function useResearcher(id: number) {
  return useSWR<ResearcherDetail>(`/api/researchers/${id}`, fetchJson);
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

export function usePublication(id: number) {
  return useSWR<PublicationDetail>(
    `/api/publications/${id}?include_history=true`,
    fetchJson
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
