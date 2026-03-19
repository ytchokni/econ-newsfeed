"use client";

import Link from "next/link";
import { useResearcher } from "@/lib/api";
import PublicationCard from "@/components/PublicationCard";
import PublicationCardSkeleton from "@/components/PublicationCardSkeleton";
import ErrorMessage from "@/components/ErrorMessage";
import EmptyState from "@/components/EmptyState";

export default function ResearcherDetailContent({ id }: { id: number }) {
  const { data: researcher, error, isLoading } = useResearcher(id);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <p className="font-sans text-sm text-[var(--text-muted)]">Loading researcher...</p>
        {Array.from({ length: 3 }).map((_, i) => (
          <PublicationCardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (error || !researcher) {
    return <ErrorMessage message={error?.message || "Researcher not found."} />;
  }

  const workingPapers = researcher.publications.filter(
    (p) => p.status !== "published"
  );
  const publications = researcher.publications.filter(
    (p) => p.status === "published"
  );

  return (
    <div>
      {/* Breadcrumb */}
      <nav className="font-sans text-sm text-[var(--text-muted)] mb-6 flex items-center gap-1.5">
        <Link href="/researchers" className="hover:text-[var(--link)] transition-colors">
          Researchers
        </Link>
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-[var(--text-primary)] font-medium">
          {researcher.first_name} {researcher.last_name}
        </span>
      </nav>

      {/* Hero card */}
      <div className="rounded-lg bg-[var(--bg-card)] shadow-card p-6 mb-8">
        <div className="flex items-start justify-between gap-4">
          <h1 className="font-serif text-2xl font-bold text-[var(--text-primary)]">
            {researcher.first_name} {researcher.last_name}
          </h1>
          {researcher.website_url && (
            <a href={researcher.website_url} target="_blank" rel="noopener noreferrer"
              className="shrink-0 inline-flex items-center gap-1.5 font-sans text-sm text-[var(--link)] hover:text-[var(--accent)] transition-colors">
              Personal website
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
              </svg>
            </a>
          )}
        </div>
        {(researcher.position || researcher.affiliation) && (
          <p className="mt-1.5 font-sans text-[var(--text-secondary)]">
            {researcher.position}
            {researcher.position && researcher.affiliation && ", "}
            {researcher.affiliation}
          </p>
        )}
        {researcher.description && (
          <p className="mt-3 font-serif text-sm text-[var(--text-secondary)] leading-relaxed">
            {researcher.description}
          </p>
        )}
        {researcher.fields && researcher.fields.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {researcher.fields.map((field) => (
              <span key={field.id} className="font-sans text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] bg-[var(--border-light)] px-2.5 py-1 rounded-full">
                {field.name}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Working Papers section (includes R&R, accepted, etc.) */}
      {workingPapers.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center gap-3 mb-4">
            <h2 className="font-serif text-lg font-semibold text-[var(--text-primary)]">
              Working Papers
            </h2>
            <span className="font-sans text-xs font-bold bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">
              {workingPapers.length}
            </span>
          </div>
          <div className="space-y-3 animate-stagger">
            {workingPapers.map((pub) => (
              <PublicationCard key={pub.id} publication={pub} primaryAuthorId={id} />
            ))}
          </div>
        </section>
      )}

      {/* Publications section (published only) */}
      {publications.length > 0 && (
        <section className="mb-8">
          <div className="flex items-center gap-3 mb-4">
            <h2 className="font-serif text-lg font-semibold text-[var(--text-primary)]">
              Publications
            </h2>
            <span className="font-sans text-xs font-bold bg-teal-100 text-teal-700 px-2 py-0.5 rounded-full">
              {publications.length}
            </span>
          </div>
          <div className="space-y-3 animate-stagger">
            {publications.map((pub) => (
              <PublicationCard key={pub.id} publication={pub} primaryAuthorId={id} />
            ))}
          </div>
        </section>
      )}

      {/* Empty state when no publications at all */}
      {publications.length === 0 && workingPapers.length === 0 && (
        <EmptyState message="No publications found for this researcher." />
      )}
    </div>
  );
}
