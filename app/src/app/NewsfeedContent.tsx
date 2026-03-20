"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePublications } from "@/lib/api";
import type { FeedFilters, Publication } from "@/lib/types";
import PublicationCard from "@/components/PublicationCard";
import PublicationCardSkeleton from "@/components/PublicationCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";
import SearchInput from "@/components/SearchInput";

/* ---------- helpers ---------- */

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
    const dateStr = pub.event_date ?? pub.discovered_at;
    const key = formatDateHeader(dateStr);
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

const INSTITUTION_OPTIONS = [
  { label: "Top 20", value: "top20" },
  { label: "MIT", value: "MIT" },
  { label: "Harvard", value: "Harvard" },
  { label: "Stanford", value: "Stanford" },
  { label: "Princeton", value: "Princeton" },
  { label: "Chicago", value: "University of Chicago" },
  { label: "Berkeley", value: "UC Berkeley" },
  { label: "Columbia", value: "Columbia" },
  { label: "Yale", value: "Yale" },
  { label: "NYU", value: "NYU" },
  { label: "Northwestern", value: "Northwestern" },
  { label: "LSE", value: "LSE" },
];

const YEAR_OPTIONS = (() => {
  const years: string[] = [];
  for (let y = 2026; y >= 2020; y--) {
    years.push(String(y));
  }
  return years;
})();

/* ---------- checkbox dropdown ---------- */

function CheckboxDropdown({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: { label: string; value: string }[];
  selected: string[];
  onChange: (selected: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const toggle = (value: string) => {
    if (selected.includes(value)) {
      onChange(selected.filter((v) => v !== value));
    } else {
      onChange([...selected, value]);
    }
  };

  const display =
    selected.length === 0
      ? label
      : selected.length <= 2
        ? options
            .filter((o) => selected.includes(o.value))
            .map((o) => o.label)
            .join(", ")
        : `${selected.length} selected`;

  const hasSelection = selected.length > 0;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-1.5 px-3 py-1.5 font-sans text-sm border rounded-lg shadow-card transition-all min-w-[120px] ${
          hasSelection
            ? "bg-[var(--bg-header)] text-white border-[var(--bg-header)]"
            : "border-[var(--border)] bg-[var(--bg-card)] hover:border-[var(--text-muted)]"
        }`}
      >
        <span>{display}</span>
        <svg
          className={`w-3.5 h-3.5 ml-auto ${hasSelection ? "text-white/70" : "text-[var(--text-muted)]"}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute z-10 mt-1 w-56 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-card-hover py-1 max-h-60 overflow-y-auto animate-dropdown-in">
          {options.map((opt) => (
            <label
              key={opt.value}
              className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-[var(--bg)] cursor-pointer font-sans"
            >
              <input
                type="checkbox"
                checked={selected.includes(opt.value)}
                onChange={() => toggle(opt.value)}
                className="rounded border-gray-300 text-gray-900 focus:ring-gray-400"
              />
              {opt.label}
            </label>
          ))}
          {selected.length > 0 && (
            <button
              onClick={() => { onChange([]); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-xs text-[var(--text-muted)] hover:bg-[var(--bg)] border-t border-[var(--border-light)] font-sans"
            >
              Clear all
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- filter bar ---------- */

function FilterBar({
  filters,
  onChange,
  searchValue,
  onSearchChange,
}: {
  filters: FeedFilters;
  onChange: (next: FeedFilters) => void;
  searchValue: string;
  onSearchChange: (value: string) => void;
}) {
  const selectedStatuses = filters.status ? filters.status.split(",") : [];
  const selectedInstitutions = (() => {
    if (filters.preset === "top20") return ["top20"];
    if (filters.institution) return filters.institution.split(",");
    return [];
  })();

  const hasActiveFilters = !!(filters.status || filters.institution || filters.preset || filters.year || searchValue);

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
      <div className="max-w-md">
        <SearchInput
          value={searchValue}
          onChange={onSearchChange}
          placeholder="Search papers by title..."
        />
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-sans text-[10px] font-bold uppercase tracking-widest text-[var(--text-muted)] mr-1">
          Filter
        </span>

        <CheckboxDropdown
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
          {YEAR_OPTIONS.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>

        <CheckboxDropdown
          label="Institution"
          options={INSTITUTION_OPTIONS}
          selected={selectedInstitutions}
          onChange={handleInstitutionChange}
        />

        {hasActiveFilters && (
          <>
            <span className="w-px h-5 bg-[var(--border)]" />
            <button
              onClick={() => { onChange({}); onSearchChange(""); }}
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
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<FeedFilters>({});
  const [searchValue, setSearchValue] = useState("");
  const { data, error, isLoading } = usePublications(page, 20, filters);

  /* Reset page to 1 whenever filters change */
  const handleFilterChange = useCallback((next: FeedFilters) => {
    setFilters(next);
    setPage(1);
  }, []);

  const handleSearchChange = useCallback((value: string) => {
    setSearchValue(value);
    setFilters((prev) => ({ ...prev, search: value || undefined }));
    setPage(1);
  }, []);

  return (
    <div className="space-y-8">
      <FilterBar
        filters={filters}
        onChange={handleFilterChange}
        searchValue={searchValue}
        onSearchChange={handleSearchChange}
      />

      {isLoading && (
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
        <EmptyState message="No new publications yet. Papers will appear here as researchers update their pages." />
      )}

      {data && data.items.length > 0 && (
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
      )}
    </div>
  );
}
