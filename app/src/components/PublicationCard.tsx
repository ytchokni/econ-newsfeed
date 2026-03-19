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

  return (
    <div className="rounded-lg bg-[var(--bg-card)] shadow-[var(--shadow-sm)] hover:shadow-[var(--shadow-md)] hover:-translate-y-px transition-all duration-200 p-5">
      {publication.status && (
        <span
          className={`inline-block text-[10px] font-semibold uppercase tracking-wider rounded-full px-2.5 py-0.5 mb-2.5 ${statusPillConfig[publication.status].className}`}
        >
          {statusPillConfig[publication.status].label}
        </span>
      )}
      <h3 className="font-serif font-semibold text-[var(--text-primary)] leading-snug">
        {publication.title}
      </h3>
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
      </p>
      {(publication.venue || publication.year) && (
        <p className="mt-1 text-sm italic text-[var(--text-muted)]">
          {publication.venue}
          {publication.venue && publication.year && ", "}
          {publication.year}
        </p>
      )}
      {publication.draft_available && publication.draft_url && (
        <p className="mt-3 flex items-center gap-2">
          <a
            href={publication.draft_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block text-xs font-medium text-[var(--accent)] border border-[var(--accent)]/30 rounded-full px-3 py-0.5 hover:bg-[var(--accent)] hover:text-white transition-colors"
          >
            Draft &#8599;
          </a>
          {publication.draft_url_status && (
            <span
              className={`inline-block text-xs rounded px-1.5 py-0.5 font-medium ${draftStatusStyles[publication.draft_url_status]}`}
            >
              {draftStatusLabels[publication.draft_url_status]}
            </span>
          )}
        </p>
      )}
      {publication.abstract && (
        <div className="mt-3">
          <button
            onClick={() => setAbstractOpen((prev) => !prev)}
            className="flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
          >
            <span
              className={`inline-block text-[9px] transition-transform duration-200 ${abstractOpen ? "rotate-90" : ""}`}
            >
              &#9658;
            </span>
            Abstract
          </button>
          {abstractOpen && (
            <p className="mt-2 text-sm text-[var(--text-secondary)] leading-relaxed bg-[#faf8f4] border-l-2 border-[var(--border-light)] rounded-r-md p-3">
              {publication.abstract}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
