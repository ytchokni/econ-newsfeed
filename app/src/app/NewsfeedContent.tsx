"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { usePublications, useJelCodes, useFilterOptions } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/publication-utils";
import type { FeedFilters, Publication } from "@/lib/types";
import PublicationCard from "@/components/PublicationCard";
import PublicationCardSkeleton from "@/components/PublicationCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";
import SearchInput from "@/components/SearchInput";
import SearchableCheckboxDropdown from "@/components/SearchableCheckboxDropdown";
import PresetBar from "@/components/PresetBar";

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

const FILTER_PARAM_KEYS = ["status", "institution", "preset", "year", "search", "jel_code", "since", "until"] as const satisfies readonly (keyof Omit<FeedFilters, "event_type">)[];

function filtersFromParams(params: URLSearchParams): FeedFilters {
  const filters: FeedFilters = {};
  for (const key of FILTER_PARAM_KEYS) {
    const val = params.get(key);
    if (val) (filters as Record<string, string>)[key] = val;
  }
  return filters;
}

function filtersToParams(
  filters: FeedFilters,
  tab: TabValue,
  page: number
): URLSearchParams {
  const params = new URLSearchParams();
  if (tab !== "new_paper") params.set("tab", tab);
  if (page > 1) params.set("page", String(page));
  for (const key of FILTER_PARAM_KEYS) {
    const val = (filters as Record<string, string | undefined>)[key];
    if (val) params.set(key, val);
  }
  return params;
}

/* ---------- constants ---------- */

const STATUS_OPTIONS = [
  { label: "Published", value: "published" },
  { label: "Accepted", value: "accepted" },
  { label: "Revise & Resubmit", value: "revise_and_resubmit" },
  { label: "Reject & Resubmit", value: "reject_and_resubmit" },
  { label: "Working Paper", value: "working_paper" },
  { label: "Work in Progress", value: "work_in_progress" },
];

const FEED_PRESETS = [
  { label: "R&R / Accepted at Top-5", value: "top5_rr_accepted" },
  { label: "Top-20 Departments", value: "top20" },
  { label: "Researchers with a Top-5", value: "has_top5" },
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
  showMyFeed,
}: {
  filters: FeedFilters;
  onChange: (next: FeedFilters) => void;
  activeTab: TabValue;
  onTabChange: (tab: TabValue) => void;
  showMyFeed: boolean;
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

  const hasActiveFilters = !!(
    filters.status || filters.institution || filters.preset ||
    filters.year || filters.search || filters.jel_code ||
    filters.since || filters.until
  );

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

  const dateInputClass =
    "px-3 py-1.5 font-sans text-sm border border-[var(--border)] rounded-lg bg-[var(--bg-card)] shadow-card focus:outline-none focus:ring-1 focus:ring-[var(--link)]";

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
          {showMyFeed && (
            <button
              onClick={() => {
                const isActive = filters.preset === "following";
                onChange({
                  ...filters,
                  preset: isActive ? undefined : "following",
                  institution: isActive ? filters.institution : undefined,
                });
              }}
              className={`font-sans text-xs font-semibold px-3 py-1 rounded-full transition-all ${
                filters.preset === "following"
                  ? "bg-[var(--accent)] text-white"
                  : "border border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
              }`}
            >
              My Feed
            </button>
          )}
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

        <div className="flex items-center gap-1.5">
          <label className="font-sans text-xs text-[var(--text-muted)]">From</label>
          <input
            type="date"
            value={filters.since ?? ""}
            onChange={(e) =>
              onChange({ ...filters, since: e.target.value || undefined })
            }
            className={dateInputClass}
          />
          <label className="font-sans text-xs text-[var(--text-muted)]">to</label>
          <input
            type="date"
            value={filters.until ?? ""}
            onChange={(e) =>
              onChange({ ...filters, until: e.target.value || undefined })
            }
            className={dateInputClass}
          />
        </div>

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
  const { isAuthenticated } = useAuth();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const [activeTab, setActiveTab] = useState<TabValue>(
    searchParams.get("tab") === "status_change" ? "status_change" : "new_paper"
  );
  const [page, setPage] = useState(Math.max(1, Number(searchParams.get("page")) || 1));
  const [filters, setFilters] = useState<FeedFilters>(() => filtersFromParams(searchParams));

  const isInitialMount = useRef(true);
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    const params = filtersToParams(filters, activeTab, page);
    const qs = params.toString();
    const next = qs ? `${pathname}?${qs}` : pathname;
    router.replace(next, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, activeTab, page, pathname]);

  const mergedFilters = useMemo<FeedFilters>(
    () => ({ ...filters, event_type: activeTab }),
    [filters, activeTab]
  );
  const { data, error, isLoading, isValidating } = usePublications(page, 20, mergedFilters);

  const handleFilterChange = useCallback((next: FeedFilters) => {
    setFilters(next);
    setPage(1);
  }, []);

  const handleTabChange = useCallback((tab: TabValue) => {
    setActiveTab(tab);
    setFilters({});
    setPage(1);
  }, []);

  return (
    <div className="space-y-6">
      <FilterBar
        filters={filters}
        onChange={handleFilterChange}
        activeTab={activeTab}
        onTabChange={handleTabChange}
        showMyFeed={isAuthenticated}
      />

      <PresetBar
        presets={FEED_PRESETS}
        active={filters.preset}
        onChange={(preset) =>
          handleFilterChange({ ...filters, preset, institution: preset ? undefined : filters.institution })
        }
      />

      {data && (
        <p className="font-sans text-sm text-[var(--text-muted)]">
          {data.total === 0
            ? "No results"
            : `Showing ${data.items.length.toLocaleString()} of ${data.total.toLocaleString()} results`}
          {data.researcher_count != null && data.researcher_count > 0
            ? ` from ${data.researcher_count.toLocaleString()} researcher${data.researcher_count === 1 ? "" : "s"}`
            : ""}
        </p>
      )}

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
