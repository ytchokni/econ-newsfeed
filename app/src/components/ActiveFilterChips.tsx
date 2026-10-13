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
    <section
      aria-label="Applied filters"
      className="mt-3 rounded-md border border-[var(--accent)]/20 bg-[var(--accent)]/5 p-3"
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-xs font-semibold text-[var(--accent)]" aria-live="polite">
          {chips.length} {chips.length === 1 ? "filter" : "filters"} applied
        </p>
        <button
          type="button"
          onClick={onClearAll}
          className="text-xs text-[var(--accent)] underline underline-offset-2 cursor-pointer hover:text-[var(--ink)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
        >
          Clear all
        </button>
      </div>
      <ul className="flex flex-wrap gap-2">
        {chips.map((chip) => (
          <li
            key={chip.key}
            className="inline-flex max-w-full items-center gap-2 rounded-full border border-[var(--accent)]/30 bg-white py-1 pl-3 pr-1 text-xs font-medium text-[var(--ink)]"
          >
            <span className="min-w-0 break-words">{chip.label}</span>
            <button
              type="button"
              onClick={chip.onRemove}
              aria-label={`Remove ${chip.label}`}
              title={`Remove ${chip.label}`}
              className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--accent)]/10 text-xl leading-none text-[var(--accent)] cursor-pointer transition-colors hover:bg-[var(--accent)] hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
            >
              <span aria-hidden="true">&times;</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
