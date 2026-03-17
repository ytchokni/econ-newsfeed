"use client";

import { useState, useEffect } from "react";
import { getResearcher } from "@/lib/api";
import type { ResearcherDetail } from "@/lib/types";
import PublicationCard from "@/components/PublicationCard";
import PublicationCardSkeleton from "@/components/PublicationCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";

export default function ResearcherDetailContent({ id }: { id: number }) {
  const [researcher, setResearcher] = useState<ResearcherDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getResearcher(id);
        if (!cancelled) {
          setResearcher(data);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("Failed to load researcher.");
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

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
    return <ErrorMessage message={error || "Researcher not found."} />;
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
