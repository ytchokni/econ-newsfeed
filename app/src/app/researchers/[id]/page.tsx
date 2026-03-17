import ResearcherDetailContent from "./ResearcherDetailContent";

export default async function ResearcherDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ResearcherDetailContent id={Number(id)} />;
}
