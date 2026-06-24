"use client";

export interface PresetOption {
  label: string;
  value: string;
  highlight?: boolean;
}

interface PresetBarProps {
  presets: PresetOption[];
  active: string | undefined;
  onChange: (preset: string | undefined) => void;
}

export default function PresetBar({ presets, active, onChange }: PresetBarProps) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="font-sans text-[10px] font-bold uppercase tracking-widest text-[var(--text-muted)] mr-1">
        Presets
      </span>
      {presets.map((p) => {
        const isActive = active === p.value;
        const highlightClasses = isActive
          ? "bg-[var(--accent)] text-white border-[var(--accent)] shadow-sm"
          : "border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white";
        const defaultClasses = isActive
          ? "bg-[var(--bg-header)] text-white border-[var(--bg-header)] shadow-sm"
          : "border-[var(--border)] text-[var(--text-secondary)] hover:border-[var(--text-muted)] hover:text-[var(--text-primary)]";

        return (
          <button
            key={p.value}
            onClick={() => onChange(isActive ? undefined : p.value)}
            className={`font-sans text-xs px-3 py-1.5 rounded-full border transition-all ${
              p.highlight ? highlightClasses : defaultClasses
            }`}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}
