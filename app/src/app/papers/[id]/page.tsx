import PaperDetailContent from "./PaperDetailContent";

export default async function PaperDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <PaperDetailContent id={Number(id)} />;
}
