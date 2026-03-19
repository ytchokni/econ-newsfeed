import Link from "next/link";
import type { Researcher } from "@/lib/types";

export default function ResearcherCard({
  researcher,
}: {
  researcher: Researcher;
}) {
  return (
    <Link
      href={`/researchers/${researcher.id}`}
      className="block rounded-lg bg-[var(--bg-card)] shadow-card p-5 hover:shadow-card-hover hover:-translate-y-px transition-all duration-200"
    >
      <h3 className="font-serif font-semibold text-[var(--text-primary)] text-lg">
        {researcher.first_name} {researcher.last_name}
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
        <p className="mt-2">
          <a
            href={researcher.website_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="font-sans text-sm text-[var(--link)] hover:underline"
          >
            Personal website &rarr;
          </a>
        </p>
      )}
    </Link>
  );
}
