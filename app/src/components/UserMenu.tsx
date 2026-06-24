"use client";

import { useState, useRef, useEffect } from "react";
import { signOut } from "next-auth/react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";

export default function UserMenu() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  if (!user) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-full hover:opacity-80 transition-opacity"
      >
        {user.image ? (
          <img
            src={user.image}
            alt=""
            className="w-7 h-7 rounded-full"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-7 h-7 rounded-full bg-[var(--accent)] flex items-center justify-center text-white text-xs font-bold">
            {(user.name || user.email || "?")[0].toUpperCase()}
          </div>
        )}
        <span className="font-sans text-xs text-[#c5cdd8] hidden sm:inline">
          {user.name?.split(" ")[0]}
        </span>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-48 rounded-lg bg-[var(--bg-card)] shadow-lg border border-[var(--border)] py-1 z-50">
          <div className="px-3 py-2 border-b border-[var(--border)]">
            <p className="font-sans text-sm font-medium text-[var(--text-primary)] truncate">
              {user.name}
            </p>
            <p className="font-sans text-xs text-[var(--text-muted)] truncate">
              {user.email}
            </p>
          </div>
          <Link
            href="/settings"
            onClick={() => setOpen(false)}
            className="block px-3 py-2 font-sans text-sm text-[var(--text-secondary)] hover:bg-[var(--border-light)] transition-colors"
          >
            Settings
          </Link>
          <button
            onClick={() => {
              setOpen(false);
              signOut();
            }}
            className="w-full text-left px-3 py-2 font-sans text-sm text-[var(--text-secondary)] hover:bg-[var(--border-light)] transition-colors"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
