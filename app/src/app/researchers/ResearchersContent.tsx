"use client";

import { useCallback, useState } from "react";
import { useFilterOptions, useResearchersFiltered } from "@/lib/api";
import type { ResearcherFilters } from "@/lib/types";
import ResearcherCard from "@/components/ResearcherCard";
import ResearcherCardSkeleton from "@/components/ResearcherCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";
import SearchableCheckboxDropdown from "@/components/SearchableCheckboxDropdown";
import SearchInput from "@/components/SearchInput";

export default function ResearchersContent() {
  const [filters, setFilters] = useState<ResearcherFilters>({});
  const { data: filterOptions } = useFilterOptions();
  const { data: researchers, error, isLoading } = useResearchersFiltered(filters);

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
    setFilters((prev) => ({ ...prev, institution: selected.join(",") || undefined }));
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

  const hasActiveFilters = !!(filters.institution || filters.field || filters.position || filters.search);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <p className="font-sans text-sm text-[var(--text-muted)]">Loading researchers...</p>
        {Array.from({ length: 3 }).map((_, i) => (
          <ResearcherCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorMessage message="Failed to load researchers." />;
  }

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

      {!researchers || researchers.length === 0 ? (
        <EmptyState message="No researchers match the current filters." />
      ) : (
        <div className="space-y-4 animate-stagger">
          {researchers.map((r) => (
            <ResearcherCard key={r.id} researcher={r} />
          ))}
        </div>
      )}
    </div>
  );
}
