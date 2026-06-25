"use client";

export interface FilterChip {
  key: string;
  label: string;
  onRemove: () => void;
}

interface ActiveFilterChipsProps {
  chips: FilterChip[];
  onClearAll: () => void;
}

export default function ActiveFilterChips({ chips, onClearAll }: ActiveFilterChipsProps) {
  if (chips.length === 0) return null;

  return (
    <div className="mt-3 flex items-center gap-2 flex-wrap">
      {chips.map((chip) => (
        <span
          key={chip.key}
          className="inline-flex items-center gap-1.5 text-[11px] text-[var(--ink2)] bg-white border border-[var(--line2)] rounded-sm px-2 py-[3px]"
        >
          {chip.label}
          <button
            onClick={chip.onRemove}
            aria-label={`Remove ${chip.label}`}
            className="text-[var(--muted)] hover:text-[var(--ink)] transition-colors leading-none"
          >
            &times;
          </button>
        </span>
      ))}
      {chips.length >= 2 && (
        <button
          onClick={onClearAll}
          className="text-[11px] text-[var(--accent)] bg-transparent border-none cursor-pointer p-0 ml-1"
        >
          Clear all
        </button>
      )}
    </div>
  );
}
