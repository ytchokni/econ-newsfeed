import { notFound } from "next/navigation";
import type { Metadata } from "next";
import PaperDetailContent from "./PaperDetailContent";
import type { PublicationDetail } from "@/lib/types";
import { BackendRateLimitedError, ssrApiHeaders } from "@/lib/ssr-api";

const API_BASE = process.env.API_INTERNAL_URL || "http://localhost:8000";

async function fetchPaper(id: number): Promise<PublicationDetail | null> {
  const path = `/api/publications/${id}?include_history=true`;
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
    return (await res.json()) as PublicationDetail;
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

  const paper = await fetchPaper(numId);
  if (!paper) return {};

  const authorNames = paper.authors
    .map((a) => `${a.first_name} ${a.last_name}`)
    .join(", ");
  const description = paper.abstract
    ? paper.abstract.slice(0, 160)
    : `${paper.title} by ${authorNames}`;

  return {
    title: paper.title,
    description,
  };
}

export default async function PaperDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const numId = Number(id);
  if (Number.isNaN(numId) || numId <= 0) {
    notFound();
  }

  const paper = await fetchPaper(numId);
  if (!paper) {
    notFound();
  }

  return <PaperDetailContent id={numId} initialData={paper} />;
}
