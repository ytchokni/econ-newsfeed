import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Page not found",
  robots: { index: false, follow: true },
};

export default function NotFound() {
  return (
    <div className="max-w-[800px] mx-auto px-6 py-16 text-center">
      <h1 className="font-serif text-2xl font-bold text-[var(--text-primary)]">Page not found</h1>
      <p className="mt-3 font-sans text-sm text-[var(--text-secondary)]">
        This researcher or paper is not (or no longer) on Econ Newsfeed.
      </p>
      <p className="mt-6 font-sans text-sm flex justify-center gap-6">
        <Link href="/" className="text-[var(--link)] hover:underline">Latest papers</Link>
        <Link href="/researchers" className="text-[var(--link)] hover:underline">Find a researcher</Link>
      </p>
    </div>
  );
}
