"use client";

import { useState } from "react";
import { usePublications } from "@/lib/api";
import type { Publication } from "@/lib/types";
import PublicationCard from "@/components/PublicationCard";
import PublicationCardSkeleton from "@/components/PublicationCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";

function formatDateHeader(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function groupByDate(publications: Publication[]) {
  const groups: Map<string, Publication[]> = new Map();
  for (const pub of publications) {
    const key = formatDateHeader(pub.discovered_at);
    const group = groups.get(key);
    if (group) {
      group.push(pub);
    } else {
      groups.set(key, [pub]);
    }
  }
  return groups;
}

export default function NewsfeedContent() {
  const [page, setPage] = useState(1);
  const { data, error, isLoading } = usePublications(page);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-gray-500">Loading publications...</p>
        {Array.from({ length: 3 }).map((_, i) => (
          <PublicationCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error && !data) {
    return <ErrorMessage message="Failed to load publications." />;
  }

  if (!data || data.items.length === 0) {
    return <EmptyState message="No publications yet." />;
  }

  const groups = groupByDate(data.items);

  return (
    <div className="space-y-6">
      {Array.from(groups.entries()).map(([date, pubs]) => (
        <section key={date}>
          <h2 className="text-sm font-medium text-gray-500 mb-3">{date}</h2>
          <div className="space-y-3">
            {pubs.map((pub) => (
              <PublicationCard key={pub.id} publication={pub} />
            ))}
          </div>
        </section>
      ))}
      <div className="flex justify-between pt-2">
        {page > 1 ? (
          <button
            onClick={() => setPage((p) => p - 1)}
            className="px-4 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-50 transition-colors"
          >
            Previous
          </button>
        ) : (
          <span />
        )}
        {data.page < data.pages && (
          <button
            onClick={() => setPage((p) => p + 1)}
            className="px-4 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-50 transition-colors"
          >
            Next
          </button>
        )}
      </div>
    </div>
  );
}
