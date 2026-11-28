import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import WeeklyActivityChart from "@/components/WeeklyActivityChart";
import { fetchBackend } from "@/lib/server-api";
import { openGraphFor, twitterFor } from "@/lib/seo";
import {
  describeInterval,
  formatCount,
  formatDateUtc,
  formatPct,
  formatShortDateUtc,
  lowWeeks,
  type SiteStats,
  type Top5Coverage,
} from "@/lib/site-stats";

const title = "About";
const description =
  "How Econ Newsfeed finds new economics papers on researchers' own websites, and how complete it is: researchers tracked, papers per week and top-5 journal coverage.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: "/about" },
  openGraph: openGraphFor({ type: "website", url: "/about", title: `About Econ Newsfeed`, description }),
  twitter: twitterFor("About Econ Newsfeed", description),
};

// The numbers come from a cached aggregate endpoint; refreshing the page every
// 15 minutes is plenty.
export const revalidate = 900;

async function loadStats(): Promise<SiteStats | null> {
  try {
    return await fetchBackend<SiteStats>("/api/stats", revalidate);
  } catch {
    // The text still renders; the numbers come back on the next revalidation.
    return null;
  }
}

const link = "text-[var(--link)] hover:underline";

function Section({ title, children, id }: { title: string; children: ReactNode; id?: string }) {
  return (
    <section id={id} className="space-y-3 scroll-mt-8">
      <h2 className="font-serif text-xl font-semibold text-[var(--ink)]">{title}</h2>
      {children}
    </section>
  );
}

function Stat({
  value,
  label,
  note,
  primary = false,
}: {
  value: string;
  label: string;
  note?: string;
  primary?: boolean;
}) {
  return (
    <div className={primary ? "border-t-2 border-[var(--ink)] pt-3" : "border-t border-[var(--line2)] pt-3"}>
      <div
        className={`font-serif font-bold leading-none text-[var(--ink)] tabular-nums ${
          primary ? "text-[34px] sm:text-[40px]" : "text-[22px] sm:text-[24px]"
        }`}
      >
        {value}
      </div>
      <div className="mt-2 text-[11px] font-semibold tracking-[0.12em] uppercase text-[var(--ink2)]">{label}</div>
      {note && <div className="mt-1 text-xs text-[var(--muted)] leading-snug">{note}</div>}
    </div>
  );
}

function StatusBreakdown({ stats }: { stats: SiteStats }) {
  const s = stats.papers.by_status;
  const rows: [string, number][] = [
    ["Working papers and work in progress", s.working_paper],
    ["Revise & resubmit", s.under_review],
    ["Accepted", s.accepted],
    ["Published", s.published],
  ];
  if (s.unknown > 0) rows.push(["Status not stated", s.unknown]);
  return (
    <table className="w-full max-w-md border-collapse text-sm tabular-nums">
      <tbody>
        {rows.map(([label, n]) => (
          <tr key={label} className="border-t border-[var(--line)]">
            <td className="py-1.5 pr-4 text-[var(--ink2)]">{label}</td>
            <td className="py-1.5 text-right text-[var(--ink)]">{formatCount(n)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Top5Section({ top5 }: { top5: Top5Coverage | null }) {
  if (!top5) {
    return (
      <p>
        We compare our data with every research article in the five most-cited general-interest
        economics journals once a week. The first comparison has not run yet; its result will appear
        here.
      </p>
    );
  }
  return (
    <>
      <p>
        To check how complete the data is, we compare it once a week with every research article
        published in the <em>American Economic Review</em>, <em>Econometrica</em>, the{" "}
        <em>Journal of Political Economy</em>, the <em>Quarterly Journal of Economics</em> and the{" "}
        <em>Review of Economic Studies</em> since {top5.from_year}, as listed by{" "}
        <a href="https://www.crossref.org/" className={link} rel="noopener">Crossref</a> (comments,
        replies, lectures, obituaries and front matter are left out). An article counts as found when we
        have a paper with its DOI or the same (or nearly the same) title, even if we still list it as
        a working paper.
      </p>
      <p>
        We found <strong>{formatCount(top5.found)}</strong> of {formatCount(top5.articles)} articles (
        <strong>{formatPct(top5.coverage_pct)}</strong>). Some articles have no author we track yet;
        for the {formatCount(top5.tracked_articles)} articles with at least one author we track, we
        found {formatPct(top5.coverage_tracked_pct)}. The remaining misses are mostly papers on pages we
        have not processed yet, papers that are not listed on the pages we track, and titles that
        changed too much to match.
      </p>
      <table className="w-full max-w-lg border-collapse text-sm tabular-nums">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-[0.1em] text-[var(--muted)]">
            <th className="font-semibold py-1.5 pr-3">Journal</th>
            <th className="font-semibold py-1.5 pr-3 text-right">Articles</th>
            <th className="font-semibold py-1.5 text-right">Share found</th>
          </tr>
        </thead>
        <tbody>
          {top5.by_journal.map((j) => (
            <tr key={j.journal} className="border-t border-[var(--line)]">
              <td className="py-1.5 pr-3 text-[var(--ink2)]">{j.name}</td>
              <td className="py-1.5 pr-3 text-right">{formatCount(j.articles)}</td>
              <td className="py-1.5 text-right text-[var(--ink)]">{formatPct(j.coverage_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-[var(--muted)]">
        Articles published {top5.from_year}–{top5.until_year}. Last compared on{" "}
        {formatDateUtc(top5.as_of)} (UTC).
      </p>
    </>
  );
}

export default async function AboutPage() {
  const stats = await loadStats();
  const top5 = stats?.top5 ?? null;
  const interval = stats ? describeInterval(stats.fetch_interval_hours) : "regularly";
  const dips = stats ? lowWeeks(stats.activity.weeks) : [];

  return (
    <article className="max-w-[800px] mx-auto px-6 py-10 font-sans text-[15px] leading-relaxed text-[var(--ink2)]">
      <h1 className="font-serif text-3xl font-bold text-[var(--ink)] mb-4">About Econ Newsfeed</h1>
      <p className="font-serif text-lg leading-relaxed text-[var(--ink)] mb-10">
        Econ Newsfeed follows economists&rsquo; own websites and tells you when a new paper appears or an
        existing one is accepted or published. Researchers usually post new work on their pages long
        before it shows up in journals or indexes; this site puts those updates in one chronological
        feed.
      </p>

      {stats ? (
        <section aria-labelledby="numbers" className="mb-12">
          <h2 id="numbers" className="sr-only">In numbers</h2>
          <div className="grid grid-cols-2 gap-x-6 gap-y-8">
            <Stat
              primary
              value={formatCount(stats.researchers.tracked)}
              label="Researchers tracked"
              note={`${formatCount(stats.researchers.pages_tracked)} web pages; ${formatCount(stats.researchers.total)} researcher records in all`}
            />
            <Stat
              primary
              value={formatCount(stats.activity.new_papers_per_week)}
              label="New papers a week"
              note="Average of the last 4 full weeks"
            />
          </div>
          <div className="mt-8 grid grid-cols-2 gap-x-6 gap-y-6">
            <Stat
              value={formatCount(stats.papers.total)}
              label="Papers in the database"
              note={`${formatCount(stats.papers.by_status.published + stats.papers.by_status.accepted)} published or accepted`}
            />
            <Stat
              value={top5 ? formatPct(top5.coverage_pct) : "–"}
              label="Top-5 articles found"
              note={
                top5
                  ? `${formatPct(top5.coverage_tracked_pct)} where we track an author; since ${top5.from_year}`
                  : "First comparison pending"
              }
            />
          </div>
          <div className="mt-10">
            <WeeklyActivityChart weeks={stats.activity.weeks} />
          </div>
          {dips.length > 0 && (
            <p className="mt-3 text-xs text-[var(--muted)]">
              {`Weeks count papers by when we found them, so a week when our processing slowed (week of ${dips
                .map((w) => formatShortDateUtc(w.week_start))
                .join(", ")}) shows as a dip that the following weeks make up.`}
            </p>
          )}
          <p className="mt-4 text-xs text-[var(--muted)]">
            Figures as of {formatDateUtc(stats.generated_at)} (UTC)
            {stats.last_fetch_at ? `; last full crawl finished ${formatDateUtc(stats.last_fetch_at)}` : ""}.
          </p>
        </section>
      ) : (
        <p className="mb-12 text-sm text-[var(--muted)]">
          The live numbers are temporarily unavailable. Please check back in a few minutes.
        </p>
      )}

      <div className="space-y-10">
        <Section title="How it works">
          <p>
            We follow the websites where economists list their own research, such as home pages and
            research pages, and download each page {interval}. When a page changes, a language model
            reads it and lists the papers on it, which tells us when a new paper appears or an existing
            one is accepted or published. Papers are then matched with{" "}
            <a href="https://openalex.org/" className={link} rel="noopener">OpenAlex</a>, an open
            catalogue of scholarly works, for DOIs, abstracts and co-authors. To correct an entry or
            opt out, see <a href="#contact" className={link}>corrections and removal</a> below.
          </p>
        </Section>

        {stats && (
          <Section title="What is in the database">
            <StatusBreakdown stats={stats} />
          </Section>
        )}

        <Section title="Coverage of the top-5 journals" id="coverage">
          <Top5Section top5={top5} />
        </Section>

        <Section title="Corrections, removal and contact" id="contact">
          <p>
            If you are listed and something is wrong, or you would rather not be listed, please get in
            touch: see <Link href="/contact" className={link}>Contact</Link>. How we handle the data,
            and how to have your profile removed, is described in the{" "}
            <Link href="/privacy" className={link}>privacy policy</Link>. Website owners can also block
            our crawler in <code className="text-[13px]">robots.txt</code>.
          </p>
          <p>
            New papers are in the <Link href="/" className={link}>feed</Link>, also available as an{" "}
            <a href="/api/feed.xml" className={link}>Atom feed</a>; signed-in readers can follow
            researchers and get a weekly email digest.
          </p>
        </Section>
      </div>
    </article>
  );
}
