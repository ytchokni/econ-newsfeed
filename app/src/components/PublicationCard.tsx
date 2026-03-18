"use client";

import { useState } from "react";
import Link from "next/link";
import type { DraftUrlStatus, Publication } from "@/lib/types";

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

export default function PublicationCard({
  publication,
}: {
  publication: Publication;
}) {
  const [abstractOpen, setAbstractOpen] = useState(false);
  const authors = publication.authors.map(formatAuthor);

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <h3 className="font-medium text-gray-900 leading-snug">
        {publication.title}
      </h3>
      <p className="mt-1 text-sm text-gray-600">
        {authors.map((a, i) => (
          <span key={a.id}>
            {i > 0 && ", "}
            <Link
              href={`/researchers/${a.id}`}
              className="hover:underline text-blue-700"
            >
              {a.display}
            </Link>
          </span>
        ))}
      </p>
      {(publication.venue || publication.year) && (
        <p className="mt-1 text-sm text-gray-500">
          {publication.venue}
          {publication.venue && publication.year && ", "}
          {publication.year}
        </p>
      )}
      {publication.draft_available && publication.draft_url && (
        <p className="mt-2 flex items-center gap-2">
          <a
            href={publication.draft_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block text-xs font-medium text-blue-700 border border-blue-300 rounded px-2 py-0.5 hover:bg-blue-50 transition-colors"
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
        <div className="mt-2">
          <button
            onClick={() => setAbstractOpen((prev) => !prev)}
            className="text-xs font-medium text-gray-500 hover:text-gray-700 transition-colors"
          >
            {abstractOpen ? "Hide abstract" : "Show abstract"}
          </button>
          {abstractOpen && (
            <p className="mt-1 text-sm text-gray-600 leading-relaxed">
              {publication.abstract}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
