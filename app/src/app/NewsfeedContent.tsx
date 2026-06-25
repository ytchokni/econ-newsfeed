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
import ActiveFilterChips from "@/components/ActiveFilterChips";
import type { FilterChip } from "@/components/ActiveFilterChips";

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

const FILTER_PARAM_KEYS = ["institution", "preset", "search", "jel_code", "since", "until"] as const satisfies readonly (keyof Omit<FeedFilters, "event_type" | "status" | "year">)[];

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
  if (tab !== "all") params.set("tab", tab);
  if (page > 1) params.set("page", String(page));
  for (const key of FILTER_PARAM_KEYS) {
    const val = (filters as Record<string, string | undefined>)[key];
    if (val) params.set(key, val);
  }
  return params;
}

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function quarterStart(d: Date): Date {
  const month = d.getMonth();
  const qMonth = month - (month % 3);
  return new Date(d.getFullYear(), qMonth, 1);
}

function formatChipDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/* ---------- constants ---------- */

const MIN_DATE = "2026-06-15";

const TAB_FILTERS: Record<TabValue, { event_type?: string; status?: string }> = {
  all: {},
  working_papers: { event_type: "new_paper", status: "working_paper" },
  publications: { event_type: "status_change", status: "revise_and_resubmit,accepted,published" },
  work_in_progress: { event_type: "new_paper", status: "work_in_progress" },
};

const PRESET_LABELS: Record<string, string> = {
  top20: "Top-20 Departments",
  has_top5: "Researchers with a Top-5",
  top5_journals: "Top-5 Journals",
  top100_repec: "Top-100 RePEc Journals",
  following: "My Feed",
};

type DatePresetKey = "7d" | "month" | "quarter";

const DATE_PRESETS: { key: DatePresetKey; label: string }[] = [
  { key: "7d", label: "Last 7 days" },
  { key: "month", label: "This month" },
  { key: "quarter", label: "This quarter" },
];

function datePresetToSince(key: DatePresetKey): string {
  const now = new Date();
  switch (key) {
    case "7d": {
      const d = new Date(now);
      d.setDate(d.getDate() - 7);
      return toISODate(d);
    }
    case "month":
      return toISODate(new Date(now.getFullYear(), now.getMonth(), 1));
    case "quarter":
      return toISODate(quarterStart(now));
  }
}

/* ---------- types ---------- */

type TabValue = "all" | "working_papers" | "publications" | "work_in_progress";

const VALID_TABS: TabValue[] = ["all", "working_papers", "publications", "work_in_progress"];

const TAB_DEFS: { key: TabValue; label: string }[] = [
  { key: "all", label: "All" },
  { key: "work_in_progress", label: "Work in Progress" },
  { key: "working_papers", label: "Working Papers" },
  { key: "publications", label: "Publications" },
];

function parseTab(value: string | null): TabValue {
  if (value && VALID_TABS.includes(value as TabValue)) return value as TabValue;
  return "all";
}

/* ---------- preset definitions per tab ---------- */

interface QuickPreset {
  value: string;
  label: string;
  highlight?: boolean;
}

function presetsForTab(tab: TabValue, isAuthenticated: boolean): QuickPreset[] {
  const base: QuickPreset[] = (() => {
    if (tab === "publications") {
      return [
        { value: "top5_journals", label: "Top-5 Journals" },
        { value: "top100_repec", label: "Top-100 RePEc" },
        { value: "top20", label: "Top-20 Depts" },
      ];
    }
    return [
      { value: "top20", label: "Top-20 Depts" },
      { value: "has_top5", label: "Has Top-5" },
    ];
  })();

  if (isAuthenticated) {
    return [{ value: "following", label: "My Feed", highlight: true }, ...base];
  }
  return base;
}

/* ---------- main component ---------- */

export default function NewsfeedContent() {
  const { isAuthenticated } = useAuth();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const [activeTab, setActiveTab] = useState<TabValue>(() =>
    parseTab(searchParams.get("tab"))
  );
  const [page, setPage] = useState(Math.max(1, Number(searchParams.get("page")) || 1));
  const [filters, setFilters] = useState<FeedFilters>(() => filtersFromParams(searchParams));
  const [datePreset, setDatePreset] = useState<DatePresetKey | null>(null);

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

  const mergedFilters = useMemo<FeedFilters>(() => {
    const tabF = TAB_FILTERS[activeTab];
    return {
      ...filters,
      ...(tabF.event_type ? { event_type: tabF.event_type as FeedFilters["event_type"] } : {}),
      ...(tabF.status ? { status: tabF.status } : {}),
    };
  }, [filters, activeTab]);

  const { data, error, isLoading, isValidating } = usePublications(page, 20, mergedFilters);

  const handleFilterChange = useCallback((next: FeedFilters) => {
    setFilters(next);
    setPage(1);
  }, []);

  const handleTabChange = useCallback((tab: TabValue) => {
    setActiveTab(tab);
    setFilters({});
    setDatePreset(null);
    setPage(1);
  }, []);

  /* ---------- filter data ---------- */

  const selectedInstitutions = filters.institution ? filters.institution.split(",") : [];
  const selectedJelCodes = filters.jel_code ? filters.jel_code.split(",") : [];

  const { data: jelCodes } = useJelCodes();
  const jelOptions = (jelCodes ?? []).map((jel) => ({
    label: `${jel.code} — ${jel.name}`,
    value: jel.code,
  }));
  const jelLabelMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const jel of jelCodes ?? []) {
      map[jel.code] = `${jel.code} — ${jel.name}`;
    }
    return map;
  }, [jelCodes]);

  const { data: filterOptions } = useFilterOptions();
  const institutionOptions = (filterOptions?.institutions ?? []).map((inst) => ({
    label: inst,
    value: inst,
  }));

  const handleJelChange = useCallback(
    (selected: string[]) => {
      handleFilterChange({ ...filters, jel_code: selected.join(",") || undefined });
    },
    [filters, handleFilterChange]
  );

  const handleInstitutionChange = useCallback(
    (selected: string[]) => {
      handleFilterChange({
        ...filters,
        institution: selected.length > 0 ? selected.join(",") : undefined,
      });
    },
    [filters, handleFilterChange]
  );

  const handlePresetClick = useCallback(
    (value: string) => {
      if (filters.preset === value) {
        handleFilterChange({ ...filters, preset: undefined });
      } else {
        handleFilterChange({ ...filters, preset: value });
      }
    },
    [filters, handleFilterChange]
  );

  const handleDatePreset = useCallback(
    (key: DatePresetKey) => {
      if (datePreset === key) {
        setDatePreset(null);
        handleFilterChange({ ...filters, since: undefined, until: undefined });
      } else {
        setDatePreset(key);
        handleFilterChange({ ...filters, since: datePresetToSince(key), until: undefined });
      }
    },
    [datePreset, filters, handleFilterChange]
  );

  const handleSinceChange = useCallback(
    (value: string) => {
      setDatePreset(null);
      handleFilterChange({ ...filters, since: value || undefined });
    },
    [filters, handleFilterChange]
  );

  const handleUntilChange = useCallback(
    (value: string) => {
      setDatePreset(null);
      handleFilterChange({ ...filters, until: value || undefined });
    },
    [filters, handleFilterChange]
  );

  const clearAll = useCallback(() => {
    setDatePreset(null);
    handleFilterChange({});
  }, [handleFilterChange]);

  const presets = useMemo(
    () => presetsForTab(activeTab, isAuthenticated),
    [activeTab, isAuthenticated]
  );

  /* ---------- build chips ---------- */

  const chips = useMemo<FilterChip[]>(() => {
    const result: FilterChip[] = [];

    if (filters.preset) {
      result.push({
        key: `preset:${filters.preset}`,
        label: PRESET_LABELS[filters.preset] ?? filters.preset,
        onRemove: () => handleFilterChange({ ...filters, preset: undefined }),
      });
    }

    for (const inst of selectedInstitutions) {
      result.push({
        key: `inst:${inst}`,
        label: inst,
        onRemove: () => {
          const remaining = selectedInstitutions.filter((i) => i !== inst);
          handleFilterChange({
            ...filters,
            institution: remaining.length > 0 ? remaining.join(",") : undefined,
          });
        },
      });
    }

    for (const code of selectedJelCodes) {
      result.push({
        key: `jel:${code}`,
        label: jelLabelMap[code] ?? code,
        onRemove: () => {
          const remaining = selectedJelCodes.filter((c) => c !== code);
          handleFilterChange({
            ...filters,
            jel_code: remaining.length > 0 ? remaining.join(",") : undefined,
          });
        },
      });
    }

    if (datePreset) {
      const presetDef = DATE_PRESETS.find((d) => d.key === datePreset);
      result.push({
        key: "date_preset",
        label: presetDef?.label ?? datePreset,
        onRemove: () => {
          setDatePreset(null);
          handleFilterChange({ ...filters, since: undefined, until: undefined });
        },
      });
    } else {
      if (filters.since) {
        result.push({
          key: "since",
          label: `Since ${formatChipDate(filters.since)}`,
          onRemove: () => handleFilterChange({ ...filters, since: undefined }),
        });
      }
      if (filters.until) {
        result.push({
          key: "until",
          label: `Until ${formatChipDate(filters.until)}`,
          onRemove: () => handleFilterChange({ ...filters, until: undefined }),
        });
      }
    }

    if (filters.search) {
      result.push({
        key: "search",
        label: `Search: ${filters.search}`,
        onRemove: () => handleFilterChange({ ...filters, search: undefined }),
      });
    }

    return result;
  }, [filters, selectedInstitutions, selectedJelCodes, jelLabelMap, datePreset, handleFilterChange]);

  return (
    <div className="max-w-[800px] mx-auto px-6">
      {/* Tabs */}
      <div className="pt-[22px]">
        <div className="flex gap-[30px] border-b border-[var(--line)]">
          {TAB_DEFS.map((t) => {
            const active = t.key === activeTab;
            return (
              <button
                key={t.key}
                onClick={() => handleTabChange(t.key)}
                className={`text-sm pb-[11px] -mb-px border-b-2 cursor-pointer transition-colors ${
                  active
                    ? "text-[var(--ink)] font-semibold border-[var(--accent)]"
                    : "text-[var(--muted)] font-medium border-transparent hover:text-[var(--ink)]"
                }`}
              >
                {t.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Search + Quick-filter presets */}
      <div className="mt-5 flex items-center justify-between gap-[18px] flex-wrap">
        <div className="flex-1 min-w-[240px] max-w-[360px]">
          <SearchInput
            value={filters.search ?? ""}
            onChange={(v) => handleFilterChange({ ...filters, search: v || undefined })}
            placeholder="Search title, author, or field..."
          />
        </div>
        <div className="flex gap-2 flex-wrap">
          {presets.map((p) => (
            <button
              key={p.value}
              onClick={() => handlePresetClick(p.value)}
              className={`text-xs font-medium tracking-[0.01em] px-[13px] py-[7px] rounded-sm cursor-pointer border transition-colors ${
                p.highlight
                  ? "border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white"
                  : "bg-transparent text-[var(--ink2)] border-[var(--line2)] hover:border-[var(--muted)]"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Filters row */}
      <div className="mt-4 flex items-center gap-3 flex-wrap">
        <span className="text-[10px] font-bold tracking-[0.16em] uppercase text-[var(--muted)]">
          Filter
        </span>

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
          {DATE_PRESETS.map((dp) => (
            <button
              key={dp.key}
              onClick={() => handleDatePreset(dp.key)}
              className={`text-[11px] font-medium px-2.5 py-[5px] rounded-sm cursor-pointer border transition-colors ${
                datePreset === dp.key
                  ? "bg-[var(--ink)] text-white border-[var(--ink)]"
                  : "bg-transparent text-[var(--ink2)] border-[var(--line2)] hover:border-[var(--muted)]"
              }`}
            >
              {dp.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-[7px] text-[var(--muted)] text-xs">
          <span>From</span>
          <input
            type="date"
            value={filters.since ?? ""}
            min={MIN_DATE}
            onChange={(e) => handleSinceChange(e.target.value)}
            className="text-xs text-[var(--ink)] border border-[var(--line2)] rounded-sm px-2 py-1.5 bg-white"
          />
          <span>to</span>
          <input
            type="date"
            value={filters.until ?? ""}
            min={MIN_DATE}
            onChange={(e) => handleUntilChange(e.target.value)}
            className="text-xs text-[var(--ink)] border border-[var(--line2)] rounded-sm px-2 py-1.5 bg-white"
          />
        </div>
      </div>

      {/* Active filter chips */}
      <ActiveFilterChips chips={chips} onClearAll={clearAll} />

      {/* Results line */}
      <div className="mt-[18px] flex items-center gap-[14px]">
        {data && (
          <p className="m-0 text-[13px] text-[var(--muted)]">
            {data.total === 0
              ? "No updates"
              : `Showing ${data.items.length.toLocaleString()} of ${data.total.toLocaleString()} updates`}
          </p>
        )}
        {chips.length === 1 && (
          <button
            onClick={clearAll}
            className="text-xs text-[var(--accent)] bg-transparent border-none cursor-pointer p-0"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Content */}
      <div className="pb-[90px]">
        {isLoading && !data && (
          <div className="mt-8 space-y-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <PublicationCardSkeleton key={i} />
            ))}
          </div>
        )}

        {error && !data && (
          <div className="mt-8">
            <ErrorMessage message="Failed to load publications." />
          </div>
        )}

        {!isLoading && data && data.items.length === 0 && (
          <EmptyState onClear={clearAll} />
        )}

        {data && data.items.length > 0 && (
          <div className={isValidating && !isLoading ? "opacity-60 transition-opacity duration-200" : "transition-opacity duration-200"}>
            {Array.from(groupByDate(data.items).entries()).map(([date, pubs]) => (
              <section key={date} className="mt-[34px]">
                <div className="flex items-center gap-[14px] mb-1.5">
                  {date === "Today" && (
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] inline-block" />
                  )}
                  <span className="text-xs font-bold tracking-[0.14em] uppercase text-[var(--ink)] whitespace-nowrap">
                    {date}
                  </span>
                  <span className="flex-1 h-px bg-[var(--line2)]" />
                  <span className="text-[11px] text-[var(--muted)] whitespace-nowrap">
                    {pubs.length} {pubs.length === 1 ? "item" : "items"}
                  </span>
                </div>

                {pubs.map((pub) => (
                  <PublicationCard key={pub.event_id ?? pub.id} publication={pub} />
                ))}
              </section>
            ))}

            {/* Pagination */}
            <div className="flex items-center justify-center gap-3 pt-8">
              {page > 1 ? (
                <button
                  onClick={() => setPage((p) => p - 1)}
                  className="px-5 py-2 text-sm font-medium border border-[var(--line2)] rounded-sm bg-white hover:border-[var(--muted)] transition-colors text-[var(--ink)]"
                >
                  &larr; Previous
                </button>
              ) : (
                <span />
              )}
              {data.pages > 0 && (
                <span className="text-sm text-[var(--muted)]">
                  Page {data.page} of {data.pages}
                </span>
              )}
              {data.page < data.pages && (
                <button
                  onClick={() => setPage((p) => p + 1)}
                  className="px-5 py-2 text-sm font-medium border border-[var(--line2)] rounded-sm bg-white hover:border-[var(--muted)] transition-colors text-[var(--ink)]"
                >
                  Next &rarr;
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
