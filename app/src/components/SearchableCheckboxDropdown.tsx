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

  const display = selected.length > 0
    ? `${label} · ${selected.length}`
    : label;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="text-[13px] text-[var(--ink2)] bg-white border border-[var(--line2)] rounded-sm px-3 py-[7px] cursor-pointer inline-flex items-center gap-2 hover:border-[var(--muted)] transition-colors"
      >
        <span>{display}</span>
        <span className="text-[9px] text-[var(--muted)]">&#9662;</span>
      </button>
      {open && (
        <>
          <div
            onClick={() => { setOpen(false); setSearch(""); }}
            className="fixed inset-0 z-[39]"
          />
          <div className="absolute top-[calc(100%+6px)] left-0 z-40 bg-white border border-[var(--line2)] rounded-[3px] shadow-card-hover min-w-[250px] max-h-[300px] overflow-auto p-[5px]">
            <div className="px-2.5 py-1.5">
              <input
                ref={inputRef}
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search..."
                className="w-full px-2.5 py-1.5 text-sm border border-[var(--line)] rounded-sm bg-white focus:outline-none placeholder:text-[var(--muted)]"
              />
            </div>
            {filtered.length === 0 && (
              <p className="px-2.5 py-2 text-sm text-[var(--muted)]">No matches</p>
            )}
            {filtered.map((opt) => (
              <label
                key={opt.value}
                className="flex items-center gap-2.5 px-2.5 py-2 text-[13px] text-[var(--ink)] cursor-pointer rounded-sm hover:bg-[#F5F5F5]"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(opt.value)}
                  onChange={() => toggle(opt.value)}
                  className="w-3.5 h-3.5 cursor-pointer flex-shrink-0"
                  style={{ accentColor: "var(--accent)" }}
                />
                <span>{opt.label}</span>
              </label>
            ))}
            {selected.length > 0 && (
              <button
                onClick={() => {
                  onChange([]);
                  setSearch("");
                }}
                className="w-full text-left px-2.5 py-1.5 text-xs text-[var(--muted)] hover:bg-[#F5F5F5] border-t border-[var(--line)]"
              >
                Clear all
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
