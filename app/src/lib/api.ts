import useSWR from "swr";
import type {
  PaginatedResponse,
  Publication,
  Researcher,
  ResearcherDetail,
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

export async function getPublications(
  page = 1,
  perPage = 20
): Promise<PaginatedResponse<Publication>> {
  return fetchJson(
    `/api/publications?page=${page}&per_page=${perPage}`
  );
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

export function usePublications(page = 1, perPage = 20) {
  return useSWR<PaginatedResponse<Publication>>(
    `/api/publications?page=${page}&per_page=${perPage}`,
    fetchJson
  );
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
