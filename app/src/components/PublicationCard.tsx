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
    <div className="rounded-lg bg-[var(--bg-card)] shadow-card hover:shadow-card-hover hover:-translate-y-px transition-all duration-200 p-5">
      {/* Status change banner (feed only) */}
      {publication.event_type === "status_change" && publication.old_status && publication.new_status && (
        <div className="flex items-center gap-2 text-xs font-medium mb-3 px-3 py-2 rounded-md bg-[#f0f4ff] border border-[#d0daf0]">
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

      {/* Authors · Venue */}
      <p className="mt-1.5 text-sm">
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
        {venueYear && (
          <>
            <span className="mx-1.5 text-[var(--text-muted)]">&middot;</span>
            <span className="italic text-[var(--text-muted)]">{venueYear}</span>
          </>
        )}
      </p>

      {/* Bottom row: status pill, draft link, abstract toggle */}
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        {publication.event_type !== "status_change" && publication.status && (
          <span
            className={`inline-block text-[10px] font-semibold uppercase tracking-wider rounded-full px-2.5 py-0.5 ${statusPillConfig[publication.status].className}`}
          >
            {statusPillConfig[publication.status].label}
          </span>
        )}
        {publication.draft_available && publication.draft_url && (
          <a
            href={publication.draft_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-[var(--accent)] border border-[var(--accent)]/30 rounded-full px-3 py-0.5 hover:bg-[var(--accent)] hover:text-white transition-colors"
          >
            Draft
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
            </svg>
          </a>
        )}
        {publication.abstract && (
          <button
            onClick={() => setAbstractOpen((prev) => !prev)}
            className="flex items-center gap-1 text-xs font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
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
        <p className="mt-3 text-sm text-[var(--text-secondary)] leading-relaxed bg-[#faf8f4] border-l-2 border-[var(--border-light)] rounded-r-md p-3">
          {publication.abstract}
        </p>
      )}
    </div>
  );
}
