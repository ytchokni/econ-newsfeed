"use client";

import { useEffect, useRef, useState } from "react";

interface SearchableCheckboxDropdownProps {
  label: string;
  options: { label: string; value: string }[];
  selected: string[];
  onChange: (selected: string[]) => void;
}

export default function SearchableCheckboxDropdown({
  label,
  options,
  selected,
  onChange,
}: SearchableCheckboxDropdownProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch("");
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open]);

  const filtered = options.filter((opt) =>
    opt.label.toLowerCase().includes(search.toLowerCase())
  );

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
        className={`flex items-center gap-1.5 px-3 py-1.5 font-sans text-sm border rounded-lg transition-all min-w-[120px] ${
          selected.length > 0
            ? "bg-[var(--bg-header)] text-white border-[var(--bg-header)]"
            : "border-[var(--border)] bg-[var(--bg-card)] shadow-card hover:border-[var(--text-muted)]"
        }`}
      >
        <span className={selected.length === 0 ? "text-[var(--text-muted)]" : ""}>
          {display}
        </span>
        <svg
          className="w-3.5 h-3.5 ml-auto opacity-50"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute z-10 mt-1 w-64 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-card-hover py-1 animate-dropdown-in">
          <div className="px-2 py-1.5">
            <input
              ref={inputRef}
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search..."
              className="w-full px-2.5 py-1.5 text-sm font-sans border border-[var(--border-light)] rounded-md bg-[var(--bg)] focus:outline-none focus:ring-1 focus:ring-[var(--link)] placeholder:text-[var(--text-muted)]"
            />
          </div>
          <div className="max-h-48 overflow-y-auto">
            {filtered.length === 0 && (
              <p className="px-3 py-2 text-sm text-[var(--text-muted)] font-sans">No matches</p>
            )}
            {filtered.map((opt) => (
              <label
                key={opt.value}
                className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-[var(--bg)] cursor-pointer font-sans"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(opt.value)}
                  onChange={() => toggle(opt.value)}
                  className="rounded border-[var(--border)] text-[var(--link)] focus:ring-[var(--link)]"
                />
                <span className="truncate">{opt.label}</span>
              </label>
            ))}
          </div>
          {selected.length > 0 && (
            <button
              onClick={() => {
                onChange([]);
                setSearch("");
              }}
              className="w-full text-left px-3 py-1.5 text-xs text-[var(--text-muted)] hover:bg-[var(--bg)] border-t border-[var(--border-light)] font-sans"
            >
              Clear all
            </button>
          )}
        </div>
      )}
    </div>
  );
}
