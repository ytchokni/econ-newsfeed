"use client";

import { useEffect, useRef } from "react";
import SearchableCheckboxDropdown from "@/components/SearchableCheckboxDropdown";

type DatePresetKey = "7d" | "month" | "quarter";

interface FilterDrawerProps {
  open: boolean;
  onClose: () => void;
  presets: { value: string; label: string }[];
  activePreset: string | undefined;
  onPresetClick: (value: string) => void;
  datePreset: DatePresetKey | null;
  onDatePreset: (key: DatePresetKey) => void;
  since: string | undefined;
  until: string | undefined;
  onSinceChange: (value: string) => void;
  onUntilChange: (value: string) => void;
  institutionOptions: { label: string; value: string }[];
  selectedInstitutions: string[];
  onInstitutionChange: (selected: string[]) => void;
  jelOptions: { label: string; value: string }[];
  selectedJelCodes: string[];
  onJelChange: (selected: string[]) => void;
  onResetAll: () => void;
  totalResults: number | null;
  minDate: string;
}

const DATE_PRESETS: { key: DatePresetKey; label: string }[] = [
  { key: "7d", label: "Last 7 days" },
  { key: "month", label: "This month" },
  { key: "quarter", label: "This quarter" },
];

export default function FilterDrawer({
  open,
  onClose,
  presets,
  activePreset,
  onPresetClick,
  datePreset,
  onDatePreset,
  since,
  until,
  onSinceChange,
  onUntilChange,
  institutionOptions,
  selectedInstitutions,
  onInstitutionChange,
  jelOptions,
  selectedJelCodes,
  onJelChange,
  onResetAll,
  totalResults,
  minDate,
}: FilterDrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  const hasActiveFilters = !!(
    activePreset || selectedInstitutions.length > 0 ||
    selectedJelCodes.length > 0 || datePreset || since || until
  );

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 bg-black/20 z-40 transition-opacity duration-200 ${
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        className={`fixed top-0 right-0 h-full w-[380px] max-w-[90vw] bg-white z-50 shadow-[-4px_0_24px_rgba(0,0,0,0.08)] flex flex-col transition-transform duration-200 ease-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-6 pb-4">
          <h2 className="text-xl font-bold text-[var(--ink)]">Filters</h2>
          <button
            onClick={onClose}
            className="text-[var(--muted)] hover:text-[var(--ink)] transition-colors text-xl leading-none cursor-pointer"
            aria-label="Close filters"
          >
            &times;
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 pb-6 space-y-7">
          {/* Quick Filters */}
          {presets.length > 0 && (
            <section>
              <h3 className="text-[10px] font-bold tracking-[0.16em] uppercase text-[var(--muted)] mb-3">
                Quick Filters
              </h3>
              <div className="flex flex-wrap gap-2">
                {presets.map((p) => (
                  <button
                    key={p.value}
                    onClick={() => onPresetClick(p.value)}
                    className={`text-xs font-medium px-[13px] py-[7px] rounded-sm cursor-pointer border transition-colors ${
                      activePreset === p.value
                        ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                        : "bg-transparent text-[var(--ink2)] border-[var(--line2)] hover:border-[var(--muted)]"
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* Date */}
          <section>
            <h3 className="text-[10px] font-bold tracking-[0.16em] uppercase text-[var(--muted)] mb-3">
              Date
            </h3>
            <div className="flex flex-wrap gap-2 mb-4">
              <button
                onClick={() => {
                  onSinceChange("");
                  onUntilChange("");
                }}
                className={`text-xs font-medium px-[13px] py-[7px] rounded-sm cursor-pointer border transition-colors ${
                  !datePreset && !since && !until
                    ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                    : "bg-transparent text-[var(--ink2)] border-[var(--line2)] hover:border-[var(--muted)]"
                }`}
              >
                All time
              </button>
              {DATE_PRESETS.map((dp) => (
                <button
                  key={dp.key}
                  onClick={() => onDatePreset(dp.key)}
                  className={`text-xs font-medium px-[13px] py-[7px] rounded-sm cursor-pointer border transition-colors ${
                    datePreset === dp.key
                      ? "bg-[var(--accent)] text-white border-[var(--accent)]"
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
                value={since ?? ""}
                min={minDate}
                onChange={(e) => onSinceChange(e.target.value)}
                className="text-xs text-[var(--ink)] border border-[var(--line2)] rounded-sm px-2 py-1.5 bg-white flex-1"
              />
              <span>to</span>
              <input
                type="date"
                value={until ?? ""}
                min={minDate}
                onChange={(e) => onUntilChange(e.target.value)}
                className="text-xs text-[var(--ink)] border border-[var(--line2)] rounded-sm px-2 py-1.5 bg-white flex-1"
              />
            </div>
          </section>

          {/* Institution */}
          <section>
            <h3 className="text-[10px] font-bold tracking-[0.16em] uppercase text-[var(--muted)] mb-3">
              Institution
            </h3>
            <SearchableCheckboxDropdown
              label={selectedInstitutions.length > 0 ? `${selectedInstitutions.length} selected` : "All"}
              options={institutionOptions}
              selected={selectedInstitutions}
              onChange={onInstitutionChange}
            />
          </section>

          {/* Field */}
          <section>
            <h3 className="text-[10px] font-bold tracking-[0.16em] uppercase text-[var(--muted)] mb-3">
              Field
            </h3>
            <SearchableCheckboxDropdown
              label={selectedJelCodes.length > 0 ? `${selectedJelCodes.length} selected` : "All"}
              options={jelOptions}
              selected={selectedJelCodes}
              onChange={onJelChange}
            />
          </section>
        </div>

        {/* Footer */}
        <div className="border-t border-[var(--line)] px-6 py-4 flex items-center justify-between">
          {hasActiveFilters ? (
            <button
              onClick={onResetAll}
              className="text-sm text-[var(--muted)] hover:text-[var(--ink)] bg-transparent border-none cursor-pointer p-0 transition-colors"
            >
              Reset all
            </button>
          ) : (
            <span />
          )}
          <button
            onClick={onClose}
            className="text-sm font-semibold text-white bg-[var(--accent)] px-5 py-2 rounded-sm cursor-pointer border-none hover:brightness-110 transition-all"
          >
            {totalResults !== null
              ? `Show ${totalResults.toLocaleString()} result${totalResults === 1 ? "" : "s"}`
              : "Show results"}
          </button>
        </div>
      </div>
    </>
  );
}
