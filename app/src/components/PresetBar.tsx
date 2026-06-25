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
    <div className="flex gap-2 flex-wrap">
      {presets.map((p) => {
        const isActive = active === p.value;
        return (
          <button
            key={p.value}
            onClick={() => onChange(isActive ? undefined : p.value)}
            className={`text-xs font-medium tracking-[0.01em] px-[13px] py-[7px] rounded-sm cursor-pointer border transition-colors ${
              isActive
                ? "bg-[var(--ink)] text-white border-[var(--ink)]"
                : "bg-transparent text-[var(--ink2)] border-[var(--line2)] hover:border-[var(--muted)]"
            }`}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}
