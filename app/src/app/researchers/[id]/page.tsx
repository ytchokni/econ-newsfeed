import { notFound } from "next/navigation";
import type { Metadata } from "next";
import ResearcherDetailContent from "./ResearcherDetailContent";
import type { ResearcherDetail } from "@/lib/types";
import { BackendRateLimitedError, ssrApiHeaders } from "@/lib/ssr-api";

const API_BASE = process.env.API_INTERNAL_URL || "http://localhost:8000";

async function fetchResearcher(id: number): Promise<ResearcherDetail | null> {
  const path = `/api/researchers/${id}`;
  // Outside the try: reading request headers must not be turned into a 404.
  const headers = await ssrApiHeaders();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { next: { revalidate: 60 }, headers });
  } catch {
    return null;
  }
  if (res.status === 429) throw new BackendRateLimitedError(path);
  if (!res.ok) return null;
  try {
    return (await res.json()) as ResearcherDetail;
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const numId = Number(id);
  if (Number.isNaN(numId) || numId <= 0) return {};

  const researcher = await fetchResearcher(numId);
  if (!researcher) return {};

  const name = `${researcher.first_name} ${researcher.last_name}`;
  const parts = [name];
  if (researcher.position) parts.push(researcher.position);
  if (researcher.affiliation) parts.push(researcher.affiliation);

  return {
    title: name,
    description: parts.join(" - "),
  };
}

export default async function ResearcherDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const numId = Number(id);
  if (Number.isNaN(numId) || numId <= 0) {
    notFound();
  }

  const researcher = await fetchResearcher(numId);
  if (!researcher) {
    notFound();
  }

  return <ResearcherDetailContent id={numId} initialData={researcher} />;
}
