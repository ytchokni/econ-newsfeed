import Link from "next/link";
import AffiliationLine from "@/components/AffiliationLine";
import type { Researcher } from "@/lib/types";
import FollowButton from "@/components/FollowButton";

export default function ResearcherCard({
  researcher,
}: {
  researcher: Researcher;
}) {
  return (
    <article className="border-b border-[var(--line)] py-[var(--rowpad)]">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-serif font-semibold text-[var(--ink)] text-lg leading-snug">
          <Link
            href={`/researchers/${researcher.id}`}
            className="hover:text-[var(--accent)] transition-colors"
          >
            {researcher.first_name} {researcher.last_name}
          </Link>
        </h3>
        <FollowButton researcherId={researcher.id} size="sm" />
      </div>

      <AffiliationLine
        position={researcher.position}
        affiliation={researcher.affiliation}
        className="mt-1 text-sm text-[var(--ink2)]"
      />

      <p className="mt-1 text-sm text-[var(--muted)]">
        {researcher.publication_count} publications tracked
        {researcher.website_url && (
          <>
            {" · "}
            <a
              href={researcher.website_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--accent)] hover:underline"
            >
              Website &rarr;
            </a>
          </>
        )}
      </p>

      {researcher.fields?.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {researcher.fields.map((field) => (
            <span
              key={field.id}
              className="text-[10px] font-semibold uppercase tracking-wider text-[var(--muted)] bg-[var(--line)] px-2 py-0.5 rounded-sm"
            >
              {field.name}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
