import { cache } from "react";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import ResearcherDetailContent from "./ResearcherDetailContent";
import JsonLd from "@/components/JsonLd";
import type { ResearcherDetail } from "@/lib/types";
import { fetchBackend } from "@/lib/server-api";
import { getCanonicalOrigin, researcherJsonLd, researcherMetadata } from "@/lib/seo";
import { getContactEmail } from "@/lib/site-info";

// 404 from the backend -> null (a real 404 page); other failures throw.
const fetchResearcher = cache((id: number) =>
  fetchBackend<ResearcherDetail>(`/api/researchers/${id}`, 60),
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
  const researcher = await fetchResearcher(numId);
  if (!researcher) return {};
  return researcherMetadata(researcher);
}

export default async function ResearcherDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const numId = parseId((await params).id);
  if (numId === null) {
    notFound();
  }

  const researcher = await fetchResearcher(numId);
  if (!researcher) {
    notFound();
  }

  return (
    <>
      <JsonLd data={researcherJsonLd(researcher)} />
      <ResearcherDetailContent
        id={numId}
        initialData={researcher}
        contactEmail={getContactEmail()}
        siteUrl={getCanonicalOrigin()}
      />
    </>
  );
}
