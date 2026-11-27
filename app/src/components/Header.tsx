"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth, useLoginModal } from "@/lib/auth";
import UserMenu from "@/components/UserMenu";

const noopSubscribe = () => () => {};

/**
 * Today's date in the visitor's time zone. Rendered only on the client: the
 * server (UTC, build or cache time) would produce a different string and a
 * hydration mismatch (React error #418).
 */
function useHeaderDate(): string {
  return useSyncExternalStore(noopSubscribe, formatHeaderDate, () => "");
}

function formatHeaderDate() {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export default function Header() {
  const pathname = usePathname();
  const { isAuthenticated, isLoading } = useAuth();
  const { openLoginModal } = useLoginModal();
  const isAdmin = pathname?.startsWith("/admin");
  const headerDate = useHeaderDate();

  if (isAdmin) {
    return (
      <header className="border-b border-[var(--line)]">
        <div className="max-w-[800px] mx-auto px-6 py-3 flex items-center justify-between">
          <Link href="/" className="text-[11px] font-semibold tracking-[0.14em] uppercase text-[var(--muted)] hover:text-[var(--ink)]">
            &larr; Back to Feed
          </Link>
        </div>
      </header>
    );
  }

  return (
    <>
      {/* Top bar */}
      <div className="border-b border-[var(--line)]">
        <div className="max-w-[800px] mx-auto px-6 py-[11px] flex items-center justify-between">
          {/* Hidden on narrow screens, where it ran into the nav (#273). */}
          <span className="hidden sm:inline text-[11px] font-semibold tracking-[0.14em] uppercase text-[var(--muted)]">
            {headerDate}
          </span>
          <div className="flex items-center gap-[26px]">
            <nav className="flex gap-[22px] text-[11px] font-semibold tracking-[0.14em] uppercase">
              <Link
                href="/"
                className={`pb-[3px] border-b-2 transition-colors ${
                  pathname === "/" || pathname === ""
                    ? "text-[var(--ink)] border-[var(--accent)]"
                    : "text-[var(--muted)] border-transparent hover:text-[var(--ink)]"
                }`}
              >
                Feed
              </Link>
              <Link
                href="/researchers"
                className={`pb-[3px] border-b-2 transition-colors ${
                  pathname?.startsWith("/researchers")
                    ? "text-[var(--ink)] border-[var(--accent)]"
                    : "text-[var(--muted)] border-transparent hover:text-[var(--ink)]"
                }`}
              >
                Researchers
              </Link>
            </nav>
            {!isLoading && (
              isAuthenticated ? (
                <UserMenu />
              ) : (
                <button
                  onClick={openLoginModal}
                  className="text-[11px] font-semibold tracking-[0.14em] uppercase text-[var(--accent)] bg-transparent border border-[var(--accent)] px-3 py-1 rounded-sm cursor-pointer hover:bg-[var(--accent)] hover:text-white transition-colors"
                >
                  Subscribe
                </button>
              )
            )}
          </div>
        </div>
      </div>

      {/* Title section */}
      <div className="border-b-2 border-[var(--ink)]">
        <div className="max-w-[800px] mx-auto px-6 py-6 flex items-end justify-between gap-7">
          <div className="flex items-center gap-[13px]">
            <span className="w-[13px] h-[13px] bg-[var(--accent)] inline-block mb-[5px]" />
            <h1 className="m-0 text-[35px] font-bold tracking-[-0.015em] leading-none">
              Econ Newsfeed
            </h1>
          </div>
          <p className="m-0 mb-[3px] font-serif italic text-sm leading-snug text-[var(--muted)] max-w-[250px] text-right">
            Stay up to date with new work from economists, the day it appears.
          </p>
        </div>
      </div>
    </>
  );
}
