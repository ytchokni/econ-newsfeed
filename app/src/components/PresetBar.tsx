"use client";

interface PresetOption {
  label: string;
  value: string;
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
      {presets.map((p) => (
        <button
          key={p.value}
          onClick={() => onChange(active === p.value ? undefined : p.value)}
          className={`font-sans text-xs px-3 py-1.5 rounded-full border transition-all ${
            active === p.value
              ? "bg-[var(--bg-header)] text-white border-[var(--bg-header)] shadow-sm"
              : "border-[var(--border)] text-[var(--text-secondary)] hover:border-[var(--text-muted)] hover:text-[var(--text-primary)]"
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}
