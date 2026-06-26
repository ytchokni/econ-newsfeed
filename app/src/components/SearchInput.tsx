"use client";

import { useEffect, useRef, useState } from "react";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export default function SearchInput({ value, onChange, placeholder = "Search..." }: SearchInputProps) {
  const [local, setLocal] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setLocal(value);
  }, [value]);

  useEffect(() => {
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, []);

  const handleChange = (next: string) => {
    setLocal(next);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => onChange(next), 300);
  };

  const handleClear = () => {
    setLocal("");
    onChange("");
    if (timerRef.current) clearTimeout(timerRef.current);
  };

  return (
    <div className="flex items-center gap-[9px] border border-[var(--line2)] rounded-sm bg-white px-3 py-[9px] text-[var(--muted)]">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="10.5" cy="10.5" r="7" />
        <line x1="21" y1="21" x2="16" y2="16" />
      </svg>
      <input
        type="text"
        value={local}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={placeholder}
        className="border-none outline-none bg-transparent text-sm text-[var(--ink)] w-full placeholder:text-[var(--muted)]"
      />
      {local && (
        <button
          onClick={handleClear}
          aria-label="Clear search"
          className="flex items-center justify-center text-[var(--muted)] hover:text-[var(--ink)] transition-colors"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  );
}
