"use client";

import { useEffect, useRef, useState } from "react";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export default function SearchInput({ value, onChange, placeholder = "Search..." }: SearchInputProps) {
  const [local, setLocal] = useState(value);
  const [previousValue, setPreviousValue] = useState(value);
  if (value !== previousValue) {
    setPreviousValue(value);
    setLocal(value);
  }
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const onChangeRef = useRef(onChange);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
  }, [value]);

  useEffect(() => {
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, []);

  const handleChange = (next: string) => {
    setLocal(next);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      onChangeRef.current(next);
    }, 300);
  };

  // Enter searches at once instead of waiting for the debounce.
  const flush = () => {
    if (!timerRef.current) return;
    clearTimeout(timerRef.current);
    timerRef.current = null;
    onChangeRef.current(local);
  };

  const handleClear = () => {
    setLocal("");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    onChange("");
    // The clear button unmounts once the field is empty; keep typing focus.
    inputRef.current?.focus();
  };

  return (
    <div className="flex items-center gap-[9px] border border-[var(--line2)] rounded-sm bg-white px-3 py-[9px] text-[var(--muted)]">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="10.5" cy="10.5" r="7" />
        <line x1="21" y1="21" x2="16" y2="16" />
      </svg>
      <input
        ref={inputRef}
        type="text"
        inputMode="search"
        enterKeyHint="search"
        autoComplete="off"
        spellCheck={false}
        aria-label={placeholder.replace(/\.+$|…$/, "")}
        value={local}
        onChange={(e) => handleChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") flush(); }}
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
