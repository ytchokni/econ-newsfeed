import { notFound } from "next/navigation";
import ResearcherDetailContent from "./ResearcherDetailContent";

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
  return <ResearcherDetailContent id={numId} />;
}
