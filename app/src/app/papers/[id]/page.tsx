import { notFound } from "next/navigation";
import type { Metadata } from "next";
import PaperDetailContent from "./PaperDetailContent";
import type { PublicationDetail } from "@/lib/types";

const API_BASE = process.env.API_INTERNAL_URL || "";

async function fetchPaper(id: number): Promise<PublicationDetail | null> {
  try {
    const res = await fetch(
      `${API_BASE}/api/publications/${id}?include_history=true`,
      { next: { revalidate: 60 } }
    );
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
