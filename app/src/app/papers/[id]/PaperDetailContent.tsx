"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { usePublication } from "@/lib/api";
import { statusPillConfig, formatAuthor } from "@/lib/publication-utils";
import type { FeedEvent, PaperSnapshot, PublicationStatus } from "@/lib/types";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function SnapshotDiff({ current, previous }: { current: PaperSnapshot; previous: PaperSnapshot }) {
  const changes: { field: string; from: string | null; to: string | null }[] = [];
  const fields: (keyof PaperSnapshot)[] = ["status", "venue", "year", "draft_url", "draft_url_status"];
  for (const field of fields) {
    if (current[field] !== previous[field]) {
      changes.push({ field, from: String(previous[field] ?? "none"), to: String(current[field] ?? "none") });
    }
  }
  if ((current.abstract ?? "") !== (previous.abstract ?? "")) {
    changes.push({
      field: "abstract",
      from: previous.abstract ? "present" : "none",
      to: current.abstract ? "present" : "none",
    });
  }
  if (changes.length === 0) return null;
  return (
    <ul className="mt-1 ml-4 text-xs text-[var(--text-muted)] space-y-0.5">
      {changes.map((c) => (
        <li key={c.field}>
          <span className="font-medium">{c.field}:</span> {c.from} &rarr; {c.to}
        </li>
      ))}
    </ul>
  );
}

function TimelineEntry({
  event,
  snapshots,
  index,
}: {
  event: FeedEvent;
  snapshots: PaperSnapshot[];
  index: number;
}) {
  const [diffOpen, setDiffOpen] = useState(false);

  const label =
    event.event_type === "new_paper"
      ? "Discovered"
      : `Status changed: ${
          event.old_status ? statusPillConfig[event.old_status as PublicationStatus]?.label ?? event.old_status : "?"
        } \u2192 ${
          event.new_status ? statusPillConfig[event.new_status as PublicationStatus]?.label ?? event.new_status : "?"
        }`;

  const eventDate = new Date(event.created_at).getTime();
  const snapshotIdx = snapshots.findIndex(
    (s) => Math.abs(new Date(s.scraped_at).getTime() - eventDate) < 86400000
  );
  const hasSnapshotDiff =
    event.event_type === "status_change" && snapshotIdx >= 0 && snapshotIdx < snapshots.length - 1;

  return (
    <div className="relative pl-6 pb-4">
      <div className="absolute left-0 top-1.5 w-2.5 h-2.5 rounded-full bg-[var(--border)] border-2 border-[var(--bg-card)]" />
      {index > 0 && (
        <div className="absolute left-[4.5px] top-4 bottom-0 w-px bg-[var(--border-light)]" />
      )}
      <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
      <p className="text-xs text-[var(--text-muted)]">{formatDate(event.created_at)}</p>
      {hasSnapshotDiff && (
        <>
          <button
            onClick={() => setDiffOpen((prev) => !prev)}
            className="text-xs text-[var(--link)] hover:underline mt-0.5"
          >
            {diffOpen ? "Hide changes" : "Show changes"}
          </button>
          {diffOpen && (
            <SnapshotDiff
              current={snapshots[snapshotIdx]}
              previous={snapshots[snapshotIdx + 1]}
            />
          )}
        </>
      )}
    </div>
  );
}

export default function PaperDetailContent({ id }: { id: number }) {
  const router = useRouter();
  const { data: publication, error, isLoading } = usePublication(id);

  if (isLoading) {
    return (
      <div className="space-y-4 animate-pulse">
        <div className="h-4 w-24 bg-[var(--border-light)] rounded" />
        <div className="h-8 w-3/4 bg-[var(--border-light)] rounded" />
        <div className="h-4 w-1/2 bg-[var(--border-light)] rounded" />
        <div className="h-32 bg-[var(--border-light)] rounded" />
      </div>
    );
  }

  if (error || !publication) {
    return (
      <div className="text-center py-12">
        <p className="text-[var(--text-muted)] mb-4">
          {error?.message || "Paper not found."}
        </p>
        <Link href="/" className="text-[var(--link)] hover:underline">
          &larr; Back to feed
        </Link>
      </div>
    );
  }

  const authors = publication.authors.map(formatAuthor);
  const venueYear = [publication.venue, publication.year].filter(Boolean).join(", ");

  const events = [...publication.feed_events].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  const snapshots = [...publication.history].sort(
    (a, b) => new Date(b.scraped_at).getTime() - new Date(a.scraped_at).getTime()
  );

  return (
    <div className="max-w-2xl mx-auto">
      <button
        onClick={() => router.back()}
        className="font-sans text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors mb-6"
      >
        &larr; Back
      </button>

      {publication.status && (
        <span
          className={`inline-block text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 mb-3 ${statusPillConfig[publication.status].className}`}
        >
          {statusPillConfig[publication.status].label}
        </span>
      )}

      <h1 className="font-serif text-2xl font-semibold text-[var(--text-primary)] leading-snug mb-2">
        {publication.title}
      </h1>

      <p className="font-sans text-sm font-medium mb-1">
        {authors.map((a, i) => (
          <span key={a.id}>
            {i > 0 && ", "}
            <Link
              href={`/researchers/${a.id}`}
              className="text-[var(--link)] hover:underline"
            >
              {a.display}
            </Link>
          </span>
        ))}
      </p>

      {venueYear && (
        <p className="font-sans text-sm italic text-[var(--text-muted)] mb-4">
          {venueYear}
        </p>
      )}

      {publication.abstract && (
        <div className="mb-4">
          <h2 className="font-sans text-xs font-bold uppercase tracking-wider text-[var(--text-muted)] mb-1.5">
            Abstract
          </h2>
          <p className="text-sm text-[var(--text-secondary)] leading-relaxed bg-[#faf8f4] border-l-2 border-[var(--border-light)] rounded-r-md p-3">
            {publication.abstract}
          </p>
        </div>
      )}

      <div className="font-sans flex items-center gap-2.5 flex-wrap mb-4">
        {publication.doi && (
          <a
            href={`https://doi.org/${publication.doi}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 bg-indigo-100 text-indigo-700 hover:bg-indigo-200 transition-colors"
          >
            DOI
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        )}
        {publication.draft_available && publication.draft_url && (
          <a
            href={publication.draft_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 bg-[#c2594b]/10 text-[#c2594b] hover:bg-[#c2594b]/20 transition-colors"
          >
            Draft
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
            </svg>
          </a>
        )}
        {publication.links?.map((link) => (
          <a
            key={link.url}
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 bg-violet-50 text-violet-700 hover:bg-violet-100 transition-colors"
          >
            {link.link_type?.toUpperCase() || "LINK"}
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
            </svg>
          </a>
        ))}
      </div>

      {publication.coauthors && publication.coauthors.length > 0 && (
        <div className="mb-6">
          <h2 className="font-sans text-xs font-bold uppercase tracking-wider text-[var(--text-muted)] mb-1.5">
            All Authors
          </h2>
          <p className="font-sans text-sm text-[var(--text-secondary)]">
            {publication.coauthors.map((ca) => ca.display_name).join(", ")}
          </p>
        </div>
      )}

      {events.length > 0 && (
        <div className="mb-6">
          <h2 className="font-sans text-xs font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">
            History
          </h2>
          <div className="border-l-2 border-[var(--border-light)] ml-1">
            {events.map((event, i) => (
              <TimelineEntry key={event.id} event={event} snapshots={snapshots} index={i} />
            ))}
          </div>
        </div>
      )}

      {process.env.NODE_ENV === "development" && (
        <details className="mb-6">
          <summary className="font-sans text-xs font-bold uppercase tracking-wider text-[var(--text-muted)] cursor-pointer hover:text-[var(--text-secondary)]">
            Technical Details
          </summary>
          <dl className="mt-2 font-sans text-xs text-[var(--text-muted)] space-y-1">
            {publication.openalex_id && (
              <>
                <dt className="font-medium inline">OpenAlex ID: </dt>
                <dd className="inline">
                  <a
                    href={`https://openalex.org/works/${publication.openalex_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[var(--link)] hover:underline"
                  >
                    {publication.openalex_id}
                  </a>
                </dd>
                <br />
              </>
            )}
            <dt className="font-medium inline">Source URL: </dt>
            <dd className="inline">{publication.source_url || "\u2014"}</dd>
            <br />
            <dt className="font-medium inline">Discovered: </dt>
            <dd className="inline">{formatDate(publication.discovered_at)}</dd>
            <br />
            <dt className="font-medium inline">Draft URL status: </dt>
            <dd className="inline">{publication.draft_url_status || "\u2014"}</dd>
            <br />
            <dt className="font-medium inline">Seed paper: </dt>
            <dd className="inline">{publication.is_seed ? "Yes" : "No"}</dd>
            <br />
            <dt className="font-medium inline">Title hash: </dt>
            <dd className="inline font-mono">{publication.title_hash}</dd>
          </dl>
        </details>
      )}
    </div>
  );
}
