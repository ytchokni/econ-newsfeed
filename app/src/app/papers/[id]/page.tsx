import { notFound } from "next/navigation";
import PaperDetailContent from "./PaperDetailContent";

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
  return <PaperDetailContent id={numId} />;
}
