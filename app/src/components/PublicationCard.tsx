"use client";

import { useState } from "react";
import Link from "next/link";
import type { Publication } from "@/lib/types";
import { statusPillConfig, formatAuthor } from "@/lib/publication-utils";

export default function PublicationCard({
  publication,
  primaryAuthorId,
}: {
  publication: Publication;
  primaryAuthorId?: number;
}) {
  const [abstractOpen, setAbstractOpen] = useState(false);
  const authors = publication.authors.map(formatAuthor);

  const venueYear = [publication.venue, publication.year].filter(Boolean).join(", ");

  return (
    <div className="rounded-md bg-[var(--bg-card)] border border-[var(--border-light)] hover:border-[var(--border)] transition-colors duration-150 px-5 py-4">
      {/* Status change banner (feed only) */}
      {publication.event_type === "status_change" && publication.old_status && publication.new_status && (
        <div className="font-sans flex items-center gap-2 text-xs font-medium mb-2.5 px-3 py-1.5 rounded bg-[#f0f4ff] border border-[#d0daf0]">
          <span className="text-[var(--text-secondary)]">Status update:</span>
          <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${statusPillConfig[publication.old_status].className}`}>
            {statusPillConfig[publication.old_status].label}
          </span>
          <span className="text-[var(--text-muted)]">&rarr;</span>
          <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${statusPillConfig[publication.new_status].className}`}>
            {statusPillConfig[publication.new_status].label}
          </span>
          {publication.venue && (
            <span className="text-[var(--text-secondary)] ml-1">at {publication.venue}</span>
          )}
        </div>
      )}

      {/* Title */}
      <h3 className="font-serif font-semibold text-[var(--text-primary)] leading-snug">
        {publication.title}
      </h3>

      {/* Authors · Venue — primary author is bold/dark, others are link-colored */}
      <p className="mt-1 font-sans text-sm font-medium">
        {authors.map((a, i) => {
          const isPrimary = primaryAuthorId != null && a.id === primaryAuthorId;
          return (
            <span key={a.id}>
              {i > 0 && ", "}
              <Link
                href={`/researchers/${a.id}`}
                className={isPrimary
                  ? "text-[var(--text-primary)] font-semibold hover:underline"
                  : "text-[var(--link)] hover:underline"
                }
              >
                {a.display}
              </Link>
            </span>
          );
        })}
        {venueYear && (
          <>
            <span className="mx-1.5 text-[var(--text-muted)] font-normal">&middot;</span>
            <span className="italic text-[var(--text-muted)] font-normal">{venueYear}</span>
          </>
        )}
      </p>

      {/* Bottom row: status pill, draft link, abstract toggle */}
      <div className="mt-2.5 font-sans flex items-center gap-2.5 flex-wrap">
        {publication.event_type !== "status_change" && publication.status && (
          <span
            className={`inline-block text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 ${statusPillConfig[publication.status].className}`}
          >
            {statusPillConfig[publication.status].label}
          </span>
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
        {publication.abstract && (
          <button
            onClick={() => setAbstractOpen((prev) => !prev)}
            className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
          >
            Abstract
            <svg
              className={`w-3 h-3 transition-transform duration-200 ${abstractOpen ? "rotate-180" : ""}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        )}
        {publication.links && publication.links.length > 0 &&
          publication.links.map((link) => (
            <a
              key={link.url}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider rounded px-2.5 py-0.5 bg-violet-50 text-violet-700 hover:bg-violet-100 transition-colors"
            >
              {link.link_type?.toUpperCase() || 'LINK'}
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
              </svg>
            </a>
          ))
        }
      </div>

      {/* OpenAlex co-authors */}
      {publication.coauthors && publication.coauthors.length > 0 && (
        <p className="mt-1.5 font-sans text-xs text-[var(--text-muted)]">
          <span className="font-medium">All authors:</span>{" "}
          {publication.coauthors.map((ca) => ca.display_name).join(", ")}
        </p>
      )}

      {/* Abstract expanded */}
      {abstractOpen && publication.abstract && (
        <p className="mt-2.5 text-sm text-[var(--text-secondary)] leading-relaxed bg-[#faf8f4] border-l-2 border-[var(--border-light)] rounded-r-md p-3">
          {publication.abstract}
        </p>
      )}
    </div>
  );
}
