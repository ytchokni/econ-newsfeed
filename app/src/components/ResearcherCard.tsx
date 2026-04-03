import Link from "next/link";
import type { Researcher } from "@/lib/types";

export default function ResearcherCard({
  researcher,
}: {
  researcher: Researcher;
}) {
  return (
    <div className="relative block rounded-lg bg-[var(--bg-card)] shadow-card p-5 hover:shadow-card-hover hover:-translate-y-px transition-all duration-200">
      {/* Stretched link covers the card for navigation */}
      <Link
        href={`/researchers/${researcher.id}`}
        className="absolute inset-0 z-0"
        aria-label={`${researcher.first_name} ${researcher.last_name}`}
        tabIndex={-1}
      />

      <h3 className="font-serif font-semibold text-[var(--text-primary)] text-lg">
        <Link href={`/researchers/${researcher.id}`} className="relative z-[1]">
          {researcher.first_name} {researcher.last_name}
        </Link>
      </h3>
      {(researcher.position || researcher.affiliation) && (
        <p className="mt-1 font-sans text-sm text-[var(--text-secondary)]">
          {researcher.position}
          {researcher.position && researcher.affiliation && ", "}
          {researcher.affiliation}
        </p>
      )}
      <p className="mt-1.5 font-sans text-sm text-[var(--text-muted)]">
        {researcher.publication_count} publications tracked
      </p>
      {researcher.fields?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {researcher.fields.map((field) => (
            <span
              key={field.id}
              className="font-sans text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)] bg-[var(--border-light)] px-2 py-0.5 rounded-full"
            >
              {field.name}
            </span>
          ))}
        </div>
      )}
      {researcher.website_url && (
        <p className="relative z-[1] mt-2">
          <a
            href={researcher.website_url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-sans text-sm text-[var(--link)] hover:underline"
          >
            Personal website &rarr;
          </a>
        </p>
      )}
    </div>
  );
}
