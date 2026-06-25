"use client";

import { useState } from "react";
import Link from "next/link";
import type { Publication } from "@/lib/types";
import { statusPillConfig, chipForStatus, formatAuthor } from "@/lib/publication-utils";

export default function PublicationCard({
  publication,
  primaryAuthorId,
}: {
  publication: Publication;
  primaryAuthorId?: number;
}) {
  const [abstractOpen, setAbstractOpen] = useState(false);
  const authors = publication.authors.map(formatAuthor);

  const isStatusChange = publication.event_type === "status_change";
  const venueYear = [publication.venue, publication.year].filter(Boolean).join(", ");

  const oldLabel = publication.old_status
    ? statusPillConfig[publication.old_status]?.label ?? publication.old_status
    : null;
  const newLabel = publication.new_status
    ? statusPillConfig[publication.new_status]?.label ?? publication.new_status
    : null;

  const fromChip = oldLabel ? chipForStatus(oldLabel) : null;
  const toChip = newLabel ? chipForStatus(newLabel) : null;

  const statusTag = !isStatusChange && publication.status
    ? statusPillConfig[publication.status]
    : null;

  return (
    <article className="py-[var(--rowpad)] border-b border-[var(--line)]">
      {/* Title with inline links */}
      <h3 className="m-0 font-sans text-xl font-semibold leading-[1.32] tracking-[-0.005em] text-[var(--ink)] max-w-[62ch]">
        <Link
          href={`/papers/${publication.id}`}
          className="hover:text-[var(--accent)] transition-colors"
        >
          {publication.title}
        </Link>
        {publication.draft_available && publication.draft_url && (
          <a
            href={publication.draft_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-base font-medium text-[var(--ink2)] ml-2.5 whitespace-nowrap hover:text-[var(--accent)] transition-colors"
            onClick={(e) => e.stopPropagation()}
          >
            (Link)
          </a>
        )}
        {publication.doi && (
          <a
            href={`https://doi.org/${publication.doi}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-base font-medium text-[var(--ink2)] ml-2.5 whitespace-nowrap hover:text-[var(--accent)] transition-colors"
            onClick={(e) => e.stopPropagation()}
          >
            (DOI)
          </a>
        )}
      </h3>

      {/* Authors and venue */}
      <p className="mt-[7px] m-0 text-sm leading-normal text-[var(--ink2)]">
        {authors.map((a, i) => (
          <span key={a.id}>
            {i > 0 && ", "}
            <Link
              href={`/researchers/${a.id}`}
              className={primaryAuthorId != null && a.id === primaryAuthorId
                ? "text-[var(--ink)] font-semibold hover:underline"
                : "hover:underline"
              }
            >
              {a.display}
            </Link>
          </span>
        ))}
        {!isStatusChange && venueYear && (
          <>
            <span className="text-[var(--muted)]">{"  ·  "}</span>
            <span className="italic text-[var(--muted)]">{venueYear}</span>
          </>
        )}
      </p>

      {/* Status change transition */}
      {isStatusChange && fromChip && toChip && oldLabel && newLabel && (
        <p className="mt-[9px] m-0 text-[13px] leading-relaxed text-[var(--muted)] flex items-center gap-[9px] flex-wrap">
          <span
            className="text-[10px] font-bold tracking-[0.1em] uppercase rounded-sm px-2 py-[3px]"
            style={{ color: fromChip.text, background: fromChip.bg, border: `1px solid ${fromChip.border}` }}
          >
            {oldLabel}
          </span>
          <span className="text-[var(--ink2)]">&rarr;</span>
          <span
            className="text-[10px] font-bold tracking-[0.1em] uppercase rounded-sm px-2 py-[3px]"
            style={{ color: toChip.text, background: toChip.bg, border: `1px solid ${toChip.border}` }}
          >
            {newLabel}
          </span>
          <span>at</span>
          <span className="italic">{venueYear}</span>
        </p>
      )}

      {/* Bottom row: status tag, abstract toggle, JEL codes */}
      <div className="mt-[13px] flex items-center gap-4 flex-wrap">
        {statusTag && (
          <span
            className="text-[10px] font-bold tracking-[0.1em] uppercase rounded-sm px-2 py-[3px]"
            style={{ color: statusTag.text, background: statusTag.bg, border: `1px solid ${statusTag.border}` }}
          >
            {statusTag.label}
          </span>
        )}
        {publication.abstract && (
          <button
            onClick={() => setAbstractOpen((prev) => !prev)}
            className="text-[11px] font-semibold tracking-[0.08em] uppercase text-[var(--ink2)] bg-transparent border-none cursor-pointer p-0 inline-flex items-center gap-[5px] hover:text-[var(--accent)] transition-colors"
          >
            <span>Abstract</span>
            <span
              className="inline-block text-[9px] transition-transform duration-200"
              style={{ transform: abstractOpen ? "rotate(180deg)" : "none" }}
            >
              &#9662;
            </span>
          </button>
        )}
        {publication.links && publication.links.length > 0 &&
          publication.links.map((link) => (
            <a
              key={link.url}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[11px] font-semibold tracking-[0.08em] uppercase text-[var(--ink2)] hover:text-[var(--accent)] transition-colors"
              onClick={(e) => e.stopPropagation()}
            >
              {link.link_type?.toUpperCase() || "LINK"}
            </a>
          ))
        }
        <span className="flex-1" />
      </div>

      {/* OpenAlex co-authors */}
      {publication.coauthors && publication.coauthors.length > 0 && (
        <p className="mt-1.5 text-xs text-[var(--muted)]">
          <span className="font-medium">All authors:</span>{" "}
          {publication.coauthors.map((ca) => ca.display_name).join(", ")}
        </p>
      )}

      {/* Abstract expanded */}
      {abstractOpen && publication.abstract && (
        <p className="mt-[14px] mb-[2px] pl-4 border-l-2 border-[var(--line2)] font-serif text-[15px] leading-relaxed text-[var(--ink2)] max-w-[68ch]">
          {publication.abstract}
        </p>
      )}
    </article>
  );
}
