"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { useFilterOptions, useResearchersFiltered } from "@/lib/api";
import type { ResearcherFilters } from "@/lib/types";
import ResearcherCard from "@/components/ResearcherCard";
import ResearcherCardSkeleton from "@/components/ResearcherCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";
import SearchableCheckboxDropdown from "@/components/SearchableCheckboxDropdown";
import SearchInput from "@/components/SearchInput";
import PresetBar from "@/components/PresetBar";

const FILTER_PARAM_KEYS = ["institution", "field", "position", "search", "preset"] as const satisfies readonly (keyof ResearcherFilters)[];

const RESEARCHER_PRESETS = [
  { label: "R&R / Accepted at Top-5", value: "top5_rr_accepted" },
  { label: "Top-20 Departments", value: "top20" },
  { label: "Researchers with a Top-5", value: "has_top5" },
];

function filtersFromParams(params: URLSearchParams): ResearcherFilters {
  const filters: ResearcherFilters = {};
  for (const key of FILTER_PARAM_KEYS) {
    const val = params.get(key);
    if (val) (filters as Record<string, string>)[key] = val;
  }
  return filters;
}

function filtersToParams(filters: ResearcherFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of FILTER_PARAM_KEYS) {
    const val = (filters as Record<string, string | undefined>)[key];
    if (val) params.set(key, val);
  }
  return params;
}

export default function ResearchersContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const [filters, setFilters] = useState<ResearcherFilters>(() =>
    filtersFromParams(searchParams)
  );
  const { data: filterOptions } = useFilterOptions();
  const { data: researchers, error, isLoading } = useResearchersFiltered(filters);

  const isInitialMount = useRef(true);
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    const params = filtersToParams(filters);
    const qs = params.toString();
    const next = qs ? `${pathname}?${qs}` : pathname;
    router.replace(next, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, pathname]);

  const institutionOptions = [
    { label: "Top 20", value: "top20" },
    ...(filterOptions?.institutions ?? []).map((i) => ({
      label: i,
      value: i,
    })),
  ];
  const positionOptions = (filterOptions?.positions ?? []).map((p) => ({
    label: p,
    value: p,
  }));
  const fieldOptions = (filterOptions?.fields ?? []).map((f) => ({
    label: f.name,
    value: f.slug,
  }));

  const selectedInstitutions = (() => {
    if (filters.preset === "top20") return ["top20"];
    if (filters.institution) return filters.institution.split(",");
    return [];
  })();
  const selectedPositions = filters.position ? filters.position.split(",") : [];
  const selectedFields = filters.field ? filters.field.split(",") : [];

  const handleInstitutionChange = useCallback((selected: string[]) => {
    const hasTop20 = selected.includes("top20");
    const institutions = selected.filter((v) => v !== "top20");
    setFilters((prev) => ({
      ...prev,
      preset: hasTop20 ? "top20" : undefined,
      institution: institutions.length > 0 ? institutions.join(",") : undefined,
    }));
  }, []);

  const handlePositionChange = useCallback((selected: string[]) => {
    setFilters((prev) => ({ ...prev, position: selected.join(",") || undefined }));
  }, []);

  const handleFieldChange = useCallback((selected: string[]) => {
    setFilters((prev) => ({ ...prev, field: selected.join(",") || undefined }));
  }, []);

  const handleSearchChange = useCallback((value: string) => {
    setFilters((prev) => ({ ...prev, search: value || undefined }));
  }, []);

  const handlePresetChange = useCallback((preset: string | undefined) => {
    setFilters((prev) => ({
      ...prev,
      preset,
      institution: preset ? undefined : prev.institution,
    }));
  }, []);

  const hasActiveFilters = !!(
    filters.institution || filters.field || filters.position ||
    filters.search || filters.preset
  );

  return (
    <div className="space-y-6">
      {/* Filter bar */}
      <div className="rounded-lg bg-[var(--bg-card)] shadow-card p-4 space-y-3">
        <div className="max-w-md">
          <SearchInput
            value={filters.search ?? ""}
            onChange={handleSearchChange}
            placeholder="Search researchers by name..."
          />
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <span className="font-sans text-[10px] font-bold uppercase tracking-widest text-[var(--text-muted)] mr-1">
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
            options={fieldOptions}
            selected={selectedFields}
            onChange={handleFieldChange}
          />
          <SearchableCheckboxDropdown
            label="Position"
            options={positionOptions}
            selected={selectedPositions}
            onChange={handlePositionChange}
          />
          {hasActiveFilters && (
            <>
              <span className="w-px h-5 bg-[var(--border)]" />
              <button
                onClick={() => setFilters({})}
                className="font-sans text-xs text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
              >
                Clear all
              </button>
            </>
          )}
        </div>
      </div>

      <PresetBar
        presets={RESEARCHER_PRESETS}
        active={filters.preset}
        onChange={handlePresetChange}
      />

      {/* Result count banner */}
      {!isLoading && researchers && (
        <p className="font-sans text-sm text-[var(--text-muted)]">
          {researchers.length === 0
            ? "No researchers match the current filters"
            : `Showing ${researchers.length.toLocaleString()} researcher${researchers.length === 1 ? "" : "s"}`}
        </p>
      )}

      {isLoading && (
        <div className="space-y-4">
          <p className="font-sans text-sm text-[var(--text-muted)]">Loading researchers...</p>
          {Array.from({ length: 3 }).map((_, i) => (
            <ResearcherCardSkeleton key={i} />
          ))}
        </div>
      )}

      {error && !researchers && (
        <ErrorMessage message="Failed to load researchers." />
      )}

      {!isLoading && researchers && researchers.length === 0 && (
        <EmptyState message="No researchers match the current filters." />
      )}

      {researchers && researchers.length > 0 && (
        <div className="space-y-4 animate-stagger">
          {researchers.map((r) => (
            <ResearcherCard key={r.id} researcher={r} />
          ))}
        </div>
      )}
    </div>
  );
}
