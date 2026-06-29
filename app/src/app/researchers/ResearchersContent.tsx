"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { useFilterOptions, useResearchersFiltered } from "@/lib/api";
import type { ResearcherFilters } from "@/lib/types";
import ResearcherCard from "@/components/ResearcherCard";
import ResearcherCardSkeleton from "@/components/ResearcherCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";
import SearchableCheckboxDropdown from "@/components/SearchableCheckboxDropdown";
import SearchInput from "@/components/SearchInput";
import ActiveFilterChips from "@/components/ActiveFilterChips";
import type { FilterChip } from "@/components/ActiveFilterChips";

const FILTER_PARAM_KEYS = ["institution", "field", "position", "search", "preset"] as const satisfies readonly (keyof ResearcherFilters)[];

const PRESET_LABELS: Record<string, string> = {
  top5_rr_accepted: "R&R / Accepted at Top-5",
  top20: "Top-20 Departments",
  has_top5: "Researchers with a Top-5",
};

const RESEARCHER_PRESETS = [
  { value: "top5_rr_accepted", label: "R&R / Accepted at Top-5" },
  { value: "top20", label: "Top-20 Depts" },
  { value: "has_top5", label: "Has Top-5" },
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

  const handleFilterChange = useCallback((next: ResearcherFilters) => {
    setFilters(next);
  }, []);

  const clearAll = useCallback(() => {
    setFilters({});
  }, []);

  const institutionOptions = (filterOptions?.institutions ?? []).map((i) => ({
    label: i,
    value: i,
  }));
  const positionOptions = (filterOptions?.positions ?? []).map((p) => ({
    label: p,
    value: p,
  }));
  const fieldOptions = (filterOptions?.fields ?? []).map((f) => ({
    label: f.name,
    value: f.slug,
  }));

  const selectedInstitutions = filters.institution ? filters.institution.split(",") : [];
  const selectedPositions = filters.position ? filters.position.split(",") : [];
  const selectedFields = filters.field ? filters.field.split(",") : [];

  const handleInstitutionChange = useCallback((selected: string[]) => {
    setFilters((prev) => ({
      ...prev,
      institution: selected.length > 0 ? selected.join(",") : undefined,
    }));
  }, []);

  const handlePositionChange = useCallback((selected: string[]) => {
    setFilters((prev) => ({ ...prev, position: selected.join(",") || undefined }));
  }, []);

  const handleFieldChange = useCallback((selected: string[]) => {
    setFilters((prev) => ({ ...prev, field: selected.join(",") || undefined }));
  }, []);

  const handlePresetClick = useCallback((value: string) => {
    setFilters((prev) => ({
      ...prev,
      preset: prev.preset === value ? undefined : value,
    }));
  }, []);

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

    for (const field of selectedFields) {
      const opt = fieldOptions.find((f) => f.value === field);
      result.push({
        key: `field:${field}`,
        label: opt?.label ?? field,
        onRemove: () => {
          const remaining = selectedFields.filter((f) => f !== field);
          handleFilterChange({
            ...filters,
            field: remaining.length > 0 ? remaining.join(",") : undefined,
          });
        },
      });
    }

    for (const pos of selectedPositions) {
      result.push({
        key: `pos:${pos}`,
        label: pos,
        onRemove: () => {
          const remaining = selectedPositions.filter((p) => p !== pos);
          handleFilterChange({
            ...filters,
            position: remaining.length > 0 ? remaining.join(",") : undefined,
          });
        },
      });
    }

    if (filters.search) {
      result.push({
        key: "search",
        label: `Search: ${filters.search}`,
        onRemove: () => handleFilterChange({ ...filters, search: undefined }),
      });
    }

    return result;
  }, [filters, selectedInstitutions, selectedFields, selectedPositions, fieldOptions, handleFilterChange]);

  return (
    <div>
      {/* Search + Quick-filter presets */}
      <div className="flex items-center justify-between gap-[18px] flex-wrap">
        <div className="flex-1 min-w-[240px] max-w-[360px]">
          <SearchInput
            value={filters.search ?? ""}
            onChange={(v) => handleFilterChange({ ...filters, search: v || undefined })}
            placeholder="Search researchers by name..."
          />
        </div>
        <div className="flex gap-2 flex-wrap">
          {RESEARCHER_PRESETS.map((p) => (
            <button
              key={p.value}
              onClick={() => handlePresetClick(p.value)}
              className="text-xs font-medium tracking-[0.01em] px-[13px] py-[7px] rounded-sm cursor-pointer border bg-transparent text-[var(--ink2)] border-[var(--line2)] hover:border-[var(--muted)] transition-colors"
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
      </div>

      {/* Active filter chips */}
      <ActiveFilterChips chips={chips} onClearAll={clearAll} />

      {/* Results line */}
      <div className="mt-[18px] flex items-center gap-[14px]">
        {!isLoading && researchers && (
          <p className="m-0 text-[13px] text-[var(--muted)]">
            {researchers.length === 0
              ? "No researchers match the current filters"
              : `Showing ${researchers.length.toLocaleString()} researcher${researchers.length === 1 ? "" : "s"}`}
          </p>
        )}
        {chips.length > 0 && (
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
        {isLoading && (
          <div className="mt-8 space-y-0">
            {Array.from({ length: 3 }).map((_, i) => (
              <ResearcherCardSkeleton key={i} />
            ))}
          </div>
        )}

        {error && !researchers && (
          <div className="mt-8">
            <ErrorMessage message="Failed to load researchers." />
          </div>
        )}

        {!isLoading && researchers && researchers.length === 0 && (
          <EmptyState message="No researchers match the current filters." onClear={clearAll} />
        )}

        {researchers && researchers.length > 0 && (
          <div className="mt-2">
            {researchers.map((r) => (
              <ResearcherCard key={r.id} researcher={r} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
