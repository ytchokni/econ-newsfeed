"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePublications } from "@/lib/api";
import type { FeedFilters, Publication } from "@/lib/types";
import PublicationCard from "@/components/PublicationCard";
import PublicationCardSkeleton from "@/components/PublicationCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";

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

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 rounded-md bg-white hover:bg-gray-50 transition-colors min-w-[120px]"
      >
        <span className={selected.length === 0 ? "text-gray-500" : "text-gray-900"}>
          {display}
        </span>
        <svg className="w-3.5 h-3.5 text-gray-400 ml-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute z-10 mt-1 w-52 bg-white border border-gray-200 rounded-md shadow-lg py-1 max-h-60 overflow-y-auto">
          {options.map((opt) => (
            <label
              key={opt.value}
              className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-50 cursor-pointer"
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
              className="w-full text-left px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-50 border-t border-gray-100"
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
}: {
  filters: FeedFilters;
  onChange: (next: FeedFilters) => void;
}) {
  const selectedStatuses = filters.status ? filters.status.split(",") : [];
  const selectedInstitutions = (() => {
    if (filters.preset === "top20") return ["top20"];
    if (filters.institution) return filters.institution.split(",");
    return [];
  })();

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
    <div className="flex items-center gap-3 mb-6">
      <CheckboxDropdown
        label="Status"
        options={STATUS_OPTIONS}
        selected={selectedStatuses}
        onChange={handleStatusChange}
      />

      <select
        value={filters.year ?? ""}
        onChange={(e) => handleYearChange(e.target.value)}
        className="px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-1 focus:ring-gray-400 bg-white"
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
    </div>
  );
}

/* ---------- main component ---------- */

export default function NewsfeedContent() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<FeedFilters>({});
  const { data, error, isLoading } = usePublications(page, 20, filters);

  /* Reset page to 1 whenever filters change */
  const handleFilterChange = useCallback((next: FeedFilters) => {
    setFilters(next);
    setPage(1);
  }, []);

  return (
    <div className="space-y-6">
      <FilterBar filters={filters} onChange={handleFilterChange} />

      {isLoading && (
        <div className="space-y-4">
          <p className="text-sm text-gray-500">Loading publications...</p>
          {Array.from({ length: 3 }).map((_, i) => (
            <PublicationCardSkeleton key={i} />
          ))}
        </div>
      )}

      {error && !data && (
        <ErrorMessage message="Failed to load publications." />
      )}

      {!isLoading && data && data.items.length === 0 && (
        <EmptyState message="No publications match the current filters." />
      )}

      {data && data.items.length > 0 && (
        <>
          {Array.from(groupByDate(data.items).entries()).map(([date, pubs]) => (
            <section key={date}>
              <h2 className="text-sm font-medium text-gray-500 mb-3">
                {date}
              </h2>
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
        </>
      )}
    </div>
  );
}
