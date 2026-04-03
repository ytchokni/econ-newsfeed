import { notFound } from "next/navigation";
import type { Metadata } from "next";
import ResearcherDetailContent from "./ResearcherDetailContent";
import type { ResearcherDetail } from "@/lib/types";

const API_BASE = process.env.API_INTERNAL_URL || "";

async function fetchResearcher(id: number): Promise<ResearcherDetail | null> {
  try {
    const res = await fetch(`${API_BASE}/api/researchers/${id}`, {
      next: { revalidate: 60 },
    });
    if (!res.ok) return null;
    return res.json();
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
