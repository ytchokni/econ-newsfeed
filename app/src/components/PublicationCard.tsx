"use client";

import { useState } from "react";
import Link from "next/link";
import type { DraftUrlStatus, Publication, PublicationStatus } from "@/lib/types";

function formatAuthor(author: { id: number; first_name: string; last_name: string }) {
  const initial = author.first_name.charAt(0);
  return { display: `${initial}. ${author.last_name}`, id: author.id };
}

const draftStatusStyles: Record<DraftUrlStatus, string> = {
  unchecked: "bg-gray-100 text-gray-500",
  valid: "bg-green-100 text-green-700",
  invalid: "bg-red-100 text-red-700",
  timeout: "bg-yellow-100 text-yellow-700",
};

const draftStatusLabels: Record<DraftUrlStatus, string> = {
  unchecked: "unchecked",
  valid: "link ok",
  invalid: "broken link",
  timeout: "timeout",
};

const statusPillConfig: Record<PublicationStatus, { label: string; className: string }> = {
  published: { label: "Published", className: "bg-teal-100 text-teal-700" },
  working_paper: { label: "Working Paper", className: "bg-blue-100 text-blue-700" },
  revise_and_resubmit: { label: "Revise & Resubmit", className: "bg-amber-100 text-amber-700" },
  reject_and_resubmit: { label: "Reject & Resubmit", className: "bg-rose-100 text-rose-700" },
  accepted: { label: "Accepted", className: "bg-emerald-100 text-emerald-700" },
};

export default function PublicationCard({
  publication,
}: {
  publication: Publication;
}) {
  const [abstractOpen, setAbstractOpen] = useState(false);
  const authors = publication.authors.map(formatAuthor);

  const venueYear = [publication.venue, publication.year].filter(Boolean).join(", ");

  return (
    <div className="rounded-md bg-[var(--bg-card)] border border-[var(--border-light)] hover:border-[var(--border)] transition-colors duration-150 px-5 py-4">
      {/* Status change banner (feed only) */}
      {publication.event_type === "status_change" && publication.old_status && publication.new_status && (
        <div className="flex items-center gap-2 text-xs font-medium mb-2.5 px-3 py-1.5 rounded bg-[#f0f4ff] border border-[#d0daf0]">
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

      {/* Authors · Venue — first author is bold/dark, co-authors are link-colored */}
      <p className="mt-1 text-sm font-medium">
        {authors.map((a, i) => (
          <span key={a.id}>
            {i > 0 && ", "}
            <Link
              href={`/researchers/${a.id}`}
              className={i === 0
                ? "text-[var(--text-primary)] font-semibold hover:underline"
                : "text-[var(--link)] hover:underline"
              }
            >
              {a.display}
            </Link>
          </span>
        ))}
        {venueYear && (
          <>
            <span className="mx-1.5 text-[var(--text-muted)] font-normal">&middot;</span>
            <span className="italic text-[var(--text-muted)] font-normal">{venueYear}</span>
          </>
        )}
      </p>

      {/* Bottom row: status pill, draft link, abstract toggle */}
      <div className="mt-2.5 flex items-center gap-2.5 flex-wrap">
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
      </div>

      {/* Abstract expanded */}
      {abstractOpen && publication.abstract && (
        <p className="mt-2.5 text-sm text-[var(--text-secondary)] leading-relaxed bg-[#faf8f4] border-l-2 border-[var(--border-light)] rounded-r-md p-3">
          {publication.abstract}
        </p>
      )}
    </div>
  );
}
