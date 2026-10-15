"use client";

import { useEffect, useRef, useState } from "react";
import { useResearcherOptions } from "@/lib/api";

export default function ResearcherDropdown({ selected, onChange }: {
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const query = search.trim();
  const ready = open && query.length >= 2 && query === debounced;
  const { data, error, isLoading } = useResearcherOptions(ready ? debounced : "");
  const { data: chosen } = useResearcherOptions("", selected.join(",") || undefined);

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => setDebounced(query), 250);
    return () => clearTimeout(timer);
  }, [query, open]);
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    function outside(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  const options = [...(chosen?.items ?? [])];
  for (const item of ready ? data?.items ?? [] : []) {
    if (!options.some((option) => option.id === item.id)) options.push(item);
  }

  return (
    <div ref={ref} className="relative" onKeyDown={(event) => {
      if (event.key === "Escape" && open) {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        buttonRef.current?.focus();
      }
    }}>
      <button ref={buttonRef} type="button" aria-label="Choose researchers" aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="text-[13px] border border-[var(--line2)] rounded-sm px-3 py-[7px]">
        {selected.length ? `${selected.length} selected` : "All researchers"} ▾
      </button>
      {open && (
        <div className="absolute top-full left-0 z-40 bg-white border border-[var(--line2)] rounded-sm shadow-card-hover min-w-[250px] max-h-[300px] overflow-auto p-2">
          <input ref={inputRef} type="search" aria-label="Search researchers" value={search}
            onChange={(event) => setSearch(event.target.value)} placeholder="Name (at least 2 characters)"
            className="w-full px-2 py-1.5 text-sm border border-[var(--line)] rounded-sm" />
          {query.length < 2 && <p className="text-xs text-[var(--muted)] py-2">Type at least 2 characters to search.</p>}
          {query.length >= 2 && (!ready || isLoading) && <p role="status" className="text-xs py-2">Searching…</p>}
          {ready && error && <p role="alert" className="text-xs py-2">Could not load researchers. Try again.</p>}
          {ready && data && data.items.length === 0 && <p className="text-xs py-2">No matches</p>}
          {options.map((option) => {
            const id = String(option.id);
            const checked = selected.includes(id);
            return (
              <label key={id} className="flex items-center gap-2 px-1 py-2 text-sm">
                <input type="checkbox" checked={checked} disabled={!checked && selected.length >= 50}
                  onChange={() => onChange(checked ? selected.filter((value) => value !== id) : [...selected, id])} />
                <span>{option.name}{option.affiliation && <small className="block text-[var(--muted)]">{option.affiliation}</small>}</span>
              </label>
            );
          })}
          {ready && data?.items.length === 20 && <p className="text-xs py-2">Showing 20 matches. Refine your search for more.</p>}
          {selected.length >= 50 && <p className="text-xs py-2">You can select up to 50 researchers.</p>}
          {selected.length > 0 && <button type="button" onClick={() => onChange([])} className="text-xs py-2">Clear researchers</button>}
        </div>
      )}
    </div>
  );
}
