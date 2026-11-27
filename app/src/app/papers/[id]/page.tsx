import { cache } from "react";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import PaperDetailContent from "./PaperDetailContent";
import JsonLd from "@/components/JsonLd";
import type { PublicationDetail } from "@/lib/types";
import { fetchBackend } from "@/lib/server-api";
import { paperJsonLd, paperMetadata } from "@/lib/seo";

// 404 from the backend -> null (a real 404 page); other failures throw.
const fetchPaper = cache((id: number) =>
  fetchBackend<PublicationDetail>(`/api/publications/${id}?include_history=true`, 60),
);

function parseId(id: string): number | null {
  return /^[1-9]\d{0,9}$/.test(id) ? Number(id) : null;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const numId = parseId((await params).id);
  if (numId === null) return {};
  const paper = await fetchPaper(numId);
  if (!paper) return {};
  return paperMetadata(paper);
}

export default async function PaperDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const numId = parseId((await params).id);
  if (numId === null) {
    notFound();
  }

  const paper = await fetchPaper(numId);
  if (!paper) {
    notFound();
  }

  return (
    <>
      <JsonLd data={paperJsonLd(paper)} />
      <PaperDetailContent id={numId} initialData={paper} />
    </>
  );
}
