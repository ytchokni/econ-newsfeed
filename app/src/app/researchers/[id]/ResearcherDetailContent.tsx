"use client";

import { useResearcher } from "@/lib/api";
import PublicationCard from "@/components/PublicationCard";
import PublicationCardSkeleton from "@/components/PublicationCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";

export default function ResearcherDetailContent({ id }: { id: number }) {
  const { data: researcher, error, isLoading } = useResearcher(id);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-gray-500">Loading researcher...</p>
        {Array.from({ length: 3 }).map((_, i) => (
          <PublicationCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error || !researcher) {
    return <ErrorMessage message={error?.message || "Researcher not found."} />;
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">
          {researcher.first_name} {researcher.last_name}
        </h1>
        {(researcher.position || researcher.affiliation) && (
          <p className="mt-1 text-gray-600">
            {researcher.position}
            {researcher.position && researcher.affiliation && ", "}
            {researcher.affiliation}
          </p>
        )}
      </div>

      <h2 className="text-lg font-medium text-gray-900 mb-4">
        Publications ({researcher.publications.length})
      </h2>

      {researcher.publications.length === 0 ? (
        <EmptyState message="No publications found for this researcher." />
      ) : (
        <div className="space-y-3">
          {researcher.publications.map((pub) => (
            <PublicationCard key={pub.id} publication={pub} />
          ))}
        </div>
      )}
    </div>
  );
}
