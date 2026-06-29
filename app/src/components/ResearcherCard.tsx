import Link from "next/link";
import type { Researcher } from "@/lib/types";
import FollowButton from "@/components/FollowButton";

export default function ResearcherCard({
  researcher,
}: {
  researcher: Researcher;
}) {
  const affParts = [researcher.position, researcher.affiliation].filter(Boolean);

  return (
    <article className="border-b border-[var(--line)] py-[18px]">
      <div className="flex items-baseline justify-between gap-4 flex-wrap">
        {/* Left: name + affiliation + fields */}
        <div className="min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="font-serif font-semibold text-[var(--ink)] text-[17px] leading-snug">
              <Link
                href={`/researchers/${researcher.id}`}
                className="hover:text-[var(--accent)] transition-colors"
              >
                {researcher.first_name} {researcher.last_name}
              </Link>
            </h3>
            {affParts.length > 0 && (
              <span className="text-sm text-[var(--ink2)]">
                {affParts.join(" · ")}
              </span>
            )}
          </div>
          {researcher.fields?.length > 0 && (
            <p className="mt-0.5 text-xs text-[var(--muted)]">
              {researcher.fields.map((f) => f.name).join(" · ")}
            </p>
          )}
        </div>

        {/* Right: paper count + site link + follow */}
        <div className="flex items-center gap-4 shrink-0 text-sm">
          <span className="text-[var(--muted)] whitespace-nowrap">
            {researcher.publication_count} {researcher.publication_count === 1 ? "paper" : "papers"}
          </span>
          {researcher.website_url && (
            <a
              href={researcher.website_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[11px] font-semibold tracking-[0.08em] uppercase text-[var(--ink2)] hover:text-[var(--accent)] transition-colors inline-flex items-center gap-1"
            >
              Site
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
              </svg>
            </a>
          )}
          <FollowButton researcherId={researcher.id} size="sm" />
        </div>
      </div>
    </article>
  );
}
