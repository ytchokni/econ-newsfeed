"use client";

import { useResearchers } from "@/lib/api";
import ResearcherCard from "@/components/ResearcherCard";
import ResearcherCardSkeleton from "@/components/ResearcherCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";

export default function ResearchersContent() {
  const { data: researchers, error, isLoading } = useResearchers();

  if (isLoading) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-gray-500">Loading researchers...</p>
        {Array.from({ length: 3 }).map((_, i) => (
          <ResearcherCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorMessage message="Failed to load researchers." />;
  }

  if (!researchers || researchers.length === 0) {
    return <EmptyState message="No researchers tracked yet." />;
  }

  return (
    <div className="space-y-3">
      {researchers.map((r) => (
        <ResearcherCard key={r.id} researcher={r} />
      ))}
    </div>
  );
}
