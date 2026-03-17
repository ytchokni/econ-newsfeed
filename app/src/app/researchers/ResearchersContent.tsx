"use client";

import { useState, useEffect } from "react";
import { getResearchers } from "@/lib/api";
import type { Researcher } from "@/lib/types";
import ResearcherCard from "@/components/ResearcherCard";
import ResearcherCardSkeleton from "@/components/ResearcherCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";

export default function ResearchersContent() {
  const [researchers, setResearchers] = useState<Researcher[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getResearchers();
        if (!cancelled) {
          setResearchers(data);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("Failed to load researchers.");
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
    return <ErrorMessage message={error} />;
  }

  if (researchers.length === 0) {
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
