"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function Header() {
  const pathname = usePathname();

  return (
    <header className="bg-[var(--bg-header)] sticky top-0 z-50 shadow-[0_2px_16px_rgba(26,35,50,0.18)]">
      <div className="mx-auto max-w-4xl px-4 sm:px-6 lg:px-8 py-5 flex items-center justify-between">
        <Link href="/" className="text-xl font-bold tracking-tight">
          <span className="sr-only">Econ Newsfeed</span>
          <span aria-hidden="true">
            <span className="text-[#f0ece4]">Econ</span>{" "}
            <span className="text-[var(--accent)]">Newsfeed</span>
          </span>
        </Link>
        <nav className="flex gap-8 font-sans text-xs font-semibold uppercase tracking-widest">
          <Link
            href="/"
            className={`py-1 border-b-2 transition-colors ${
              pathname === "/"
                ? "text-[#f0ece4] border-[var(--accent)]"
                : "text-[#8896a7] border-transparent hover:text-[#f0ece4]"
            }`}
          >
            Feed
          </Link>
          <Link
            href="/researchers"
            className={`py-1 border-b-2 transition-colors ${
              pathname?.startsWith("/researchers")
                ? "text-[#f0ece4] border-[var(--accent)]"
                : "text-[#8896a7] border-transparent hover:text-[#f0ece4]"
            }`}
          >
            Researchers
          </Link>
        </nav>
      </div>
    </header>
  );
}
