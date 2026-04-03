"use client";

import { useCallback, useEffect, useState } from "react";
import { usePublications, useJelCodes, useFilterOptions } from "@/lib/api";
import { formatDate } from "@/lib/publication-utils";
import type { FeedFilters, Publication } from "@/lib/types";
import PublicationCard from "@/components/PublicationCard";
import PublicationCardSkeleton from "@/components/PublicationCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";
import SearchInput from "@/components/SearchInput";
import SearchableCheckboxDropdown from "@/components/SearchableCheckboxDropdown";

/* ---------- helpers ---------- */

function groupByDate(publications: Publication[]) {
  const groups: Map<string, Publication[]> = new Map();
  for (const pub of publications) {
    const dateStr = pub.event_date ?? pub.discovered_at;
    const key = formatDate(dateStr);
    const group = groups.get(key);
    if (group) {
      group.push(pub);
    } else {
      groups.set(key, [pub]);
    }
  }
  return groups;
}

/* ---------- constants ---------- */

const STATUS_OPTIONS = [
  { label: "Published", value: "published" },
  { label: "Accepted", value: "accepted" },
  { label: "Revise & Resubmit", value: "revise_and_resubmit" },
  { label: "Working Paper", value: "working_paper" },
];

function getYearOptions(): string[] {
  const currentYear = new Date().getFullYear();
  const years: string[] = [];
  for (let y = currentYear; y >= 2020; y--) {
    years.push(String(y));
  }
  return years;
}

/* ---------- types ---------- */

type TabValue = "new_paper" | "status_change";

/* ---------- filter bar ---------- */

function FilterBar({
  filters,
  onChange,
  activeTab,
  onTabChange,
}: {
  filters: FeedFilters;
  onChange: (next: FeedFilters) => void;
  activeTab: TabValue;
  onTabChange: (tab: TabValue) => void;
}) {
  const selectedStatuses = filters.status ? filters.status.split(",") : [];
  const selectedInstitutions = (() => {
    if (filters.preset === "top20") return ["top20"];
    if (filters.institution) return filters.institution.split(",");
    return [];
  })();

  const selectedJelCodes = filters.jel_code ? filters.jel_code.split(",") : [];
  const { data: jelCodes } = useJelCodes();
  const jelOptions = (jelCodes ?? []).map((jel) => ({
    label: `${jel.code} — ${jel.name}`,
    value: jel.code,
  }));

  const { data: filterOptions } = useFilterOptions();
  const institutionOptions = [
    { label: "Top 20", value: "top20" },
    ...(filterOptions?.institutions ?? []).map((inst) => ({
      label: inst,
      value: inst,
    })),
  ];

  const yearOptions = getYearOptions();

  const hasActiveFilters = !!(filters.status || filters.institution || filters.preset || filters.year || filters.search || filters.jel_code);

  const handleJelChange = useCallback(
    (selected: string[]) => {
      onChange({ ...filters, jel_code: selected.join(",") || undefined });
    },
    [filters, onChange]
  );

  const handleStatusChange = useCallback(
    (selected: string[]) => {
      onChange({ ...filters, status: selected.join(",") || undefined });
    },
    [filters, onChange]
  );

  const handleInstitutionChange = useCallback(
    (selected: string[]) => {
      const hasTop20 = selected.includes("top20");
      const institutions = selected.filter((v) => v !== "top20");
      onChange({
        ...filters,
        preset: hasTop20 ? "top20" : undefined,
        institution: institutions.length > 0 ? institutions.join(",") : undefined,
      });
    },
    [filters, onChange]
  );

  const handleYearChange = useCallback(
    (year: string) => {
      onChange({ ...filters, year: year || undefined });
    },
    [filters, onChange]
  );

  return (
    <div className="rounded-lg bg-[var(--bg-card)] shadow-card p-4 mb-8 space-y-3">
      {/* Event type toggle */}
      <div className="flex items-center gap-2">
        <div className="inline-flex bg-[var(--bg)] rounded-lg p-0.5">
          <button
            onClick={() => onTabChange("new_paper")}
            aria-pressed={activeTab === "new_paper"}
            className={`px-4 py-1.5 text-sm font-medium rounded-md transition-all ${
              activeTab === "new_paper"
                ? "bg-[var(--bg-header)] text-white shadow-sm"
                : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
            }`}
          >
            New Projects
          </button>
          <button
            onClick={() => onTabChange("status_change")}
            aria-pressed={activeTab === "status_change"}
            className={`px-4 py-1.5 text-sm font-medium rounded-md transition-all ${
              activeTab === "status_change"
                ? "bg-[var(--bg-header)] text-white shadow-sm"
                : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
            }`}
          >
            Status Changes
          </button>
        </div>
      </div>
      <div className="max-w-md">
        <SearchInput
          value={filters.search ?? ""}
          onChange={(v) => onChange({ ...filters, search: v || undefined })}
          placeholder="Search papers by title..."
        />
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-sans text-[10px] font-bold uppercase tracking-widest text-[var(--text-muted)] mr-1">
          Filter
        </span>

        <SearchableCheckboxDropdown
          label="Status"
          options={STATUS_OPTIONS}
          selected={selectedStatuses}
          onChange={handleStatusChange}
        />

        <select
          value={filters.year ?? ""}
          onChange={(e) => handleYearChange(e.target.value)}
          className="px-3 py-1.5 font-sans text-sm border border-[var(--border)] rounded-lg bg-[var(--bg-card)] shadow-card focus:outline-none focus:ring-1 focus:ring-[var(--link)]"
        >
          <option value="">All years</option>
          {yearOptions.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>

        <SearchableCheckboxDropdown
          label="Institution"
          options={institutionOptions}
          selected={selectedInstitutions}
          onChange={handleInstitutionChange}
        />

        <SearchableCheckboxDropdown
          label="Field"
          options={jelOptions}
          selected={selectedJelCodes}
          onChange={handleJelChange}
        />

        {hasActiveFilters && (
          <>
            <span className="w-px h-5 bg-[var(--border)]" />
            <button
              onClick={() => onChange({})}
              className="font-sans text-xs text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
            >
              Clear all
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/* ---------- main component ---------- */

export default function NewsfeedContent() {
  const [activeTab, setActiveTab] = useState<TabValue>("new_paper");
  const [page, setPage] = useState(1);

  /* Sync tab from URL on mount (avoids SSR hydration mismatch) */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const tab = params.get("tab");
    if (tab === "status_change") {
      setActiveTab("status_change");
    }
  }, []);
  const [filters, setFilters] = useState<FeedFilters>({});
  const mergedFilters = { ...filters, event_type: activeTab };
  const { data, error, isLoading, isValidating } = usePublications(page, 20, mergedFilters);

  /* Reset page to 1 whenever filters change */
  const handleFilterChange = useCallback((next: FeedFilters) => {
    setFilters(next);
    setPage(1);
  }, []);

  const handleTabChange = useCallback((tab: TabValue) => {
    setActiveTab(tab);
    setFilters({});
    setPage(1);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", tab);
      window.history.replaceState({}, "", url.toString());
    }
  }, []);

  return (
    <div className="space-y-8">
      <FilterBar
        filters={filters}
        onChange={handleFilterChange}
        activeTab={activeTab}
        onTabChange={handleTabChange}
      />

      {isLoading && !data && (
        <div className="space-y-4">
          <p className="font-sans text-sm text-[var(--text-muted)]">Loading publications...</p>
          {Array.from({ length: 3 }).map((_, i) => (
            <PublicationCardSkeleton key={i} />
          ))}
        </div>
      )}

      {error && !data && (
        <ErrorMessage message="Failed to load publications." />
      )}

      {!isLoading && data && data.items.length === 0 && (
        <EmptyState
          message={
            activeTab === "new_paper"
              ? "No new publications yet. Papers will appear here as researchers update their pages."
              : "No status changes yet. Updates will appear here when papers change status."
          }
        />
      )}

      {data && data.items.length > 0 && (
        <div className={isValidating && !isLoading ? "opacity-60 transition-opacity duration-200" : "transition-opacity duration-200"}>
          <>
            {Array.from(groupByDate(data.items).entries()).map(([date, pubs]) => (
              <section key={date}>
                <h2 className="font-sans text-xs font-semibold uppercase tracking-widest text-[var(--text-muted)] mb-4 pb-2 border-b border-[var(--border-light)] flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)]" />
                  {date}
                </h2>
                <div className="space-y-3 animate-stagger">
                  {pubs.map((pub) => (
                    <PublicationCard key={pub.event_id ?? pub.id} publication={pub} />
                  ))}
                </div>
              </section>
            ))}
            <div className="flex items-center justify-center gap-3 pt-4">
              {page > 1 ? (
                <button
                  onClick={() => setPage((p) => p - 1)}
                  className="font-sans px-5 py-2 text-sm font-medium border border-[var(--border)] rounded-lg bg-[var(--bg-card)] shadow-card hover:shadow-card-hover hover:-translate-y-px transition-all duration-200 text-[var(--text-primary)]"
                >
                  &larr; Previous
                </button>
              ) : (
                <span />
              )}
              {data && data.pages > 0 && (
                <span className="font-sans text-sm text-[var(--text-muted)]">
                  Page {data.page} of {data.pages}
                </span>
              )}
              {data && data.page < data.pages && (
                <button
                  onClick={() => setPage((p) => p + 1)}
                  className="font-sans px-5 py-2 text-sm font-medium border border-[var(--border)] rounded-lg bg-[var(--bg-card)] shadow-card hover:shadow-card-hover hover:-translate-y-px transition-all duration-200 text-[var(--text-primary)]"
                >
                  Next &rarr;
                </button>
              )}
            </div>
          </>
        </div>
      )}
    </div>
  );
}
