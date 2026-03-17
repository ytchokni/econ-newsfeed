"use client";

import { useState, useEffect, useCallback } from "react";
import { getPublications } from "@/lib/api";
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
  const [publications, setPublications] = useState<Publication[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPage = useCallback(async (pageNum: number) => {
    setIsLoading(true);
    try {
      const data = await getPublications(pageNum);
      setPublications((prev) => {
        const existingIds = new Set(prev.map((p) => p.id));
        const newItems = data.items.filter((p) => !existingIds.has(p.id));
        return [...prev, ...newItems];
      });
      setHasMore(data.page < data.pages);
      setError(null);
    } catch {
      setError("Failed to load publications.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPage(page);
  }, [page, fetchPage]);

  const loadMore = useCallback(() => {
    setPage((p) => p + 1);
  }, []);

  if (isLoading && publications.length === 0) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-gray-500">Loading publications...</p>
        {Array.from({ length: 3 }).map((_, i) => (
          <PublicationCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error && publications.length === 0) {
    return <ErrorMessage message={error} />;
  }

  if (!isLoading && publications.length === 0) {
    return <EmptyState message="No publications yet." />;
  }

  const groups = groupByDate(publications);

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
      {hasMore && (
        <div className="text-center pt-2">
          <button
            onClick={loadMore}
            disabled={isLoading}
            className="px-4 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            {isLoading ? "Loading..." : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}
