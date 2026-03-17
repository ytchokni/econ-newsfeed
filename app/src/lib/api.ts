import type {
  PaginatedResponse,
  Publication,
  ResearchField,
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
    `${API_BASE_URL}/api/publications?page=${page}&per_page=${perPage}`
  );
}

export async function getResearchers(): Promise<Researcher[]> {
  const data = await fetchJson<{ items: Researcher[] }>(
    `${API_BASE_URL}/api/researchers`
  );
  return data.items;
}

export async function getResearcher(id: number): Promise<ResearcherDetail> {
  return fetchJson(`${API_BASE_URL}/api/researchers/${id}`);
}

export async function getFields(): Promise<ResearchField[]> {
  const data = await fetchJson<{ items: ResearchField[] }>(
    `${API_BASE_URL}/api/fields`
  );
  return data.items;
}
