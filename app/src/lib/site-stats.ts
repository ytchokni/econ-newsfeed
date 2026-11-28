// Shape of GET /api/stats (backend/database/site_stats.py) and pure formatting
// helpers for the About page. Dates are always formatted in UTC so server and
// client (and every cache) agree.

export interface WeeklyActivity {
  week_start: string; // YYYY-MM-DD, Monday (UTC)
  partial: boolean; // the current, unfinished week
  new_papers: number;
  status_changes: number;
  events: number;
}

export interface JournalCoverage {
  journal: string;
  name: string;
  articles: number;
  found: number;
  coverage_pct: number | null;
  coverage_tracked_pct: number | null;
}

export interface Top5Coverage {
  as_of: string;
  from_year: number;
  until_year: number;
  articles: number;
  found: number;
  coverage_pct: number | null;
  tracked_articles: number;
  tracked_found: number;
  coverage_tracked_pct: number | null;
  correct_record_pct: number | null;
  by_journal: JournalCoverage[];
}

export interface SiteStats {
  generated_at: string;
  researchers: { total: number; tracked: number; pages_tracked: number };
  papers: {
    total: number;
    by_status: {
      working_paper: number;
      under_review: number;
      accepted: number;
      published: number;
      unknown: number;
    };
  };
  activity: { weeks: WeeklyActivity[]; new_papers_per_week: number; events_per_week: number };
  top5: Top5Coverage | null;
  fetch_interval_hours: number;
  last_fetch_at: string | null;
  last_extraction_at: string | null;
}

const numberFormat = new Intl.NumberFormat("en-US");

export function formatCount(n: number): string {
  return numberFormat.format(n);
}

export function formatPct(p: number | null | undefined): string {
  return p === null || p === undefined ? "–" : `${p.toFixed(1)}%`;
}

const dateFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  day: "numeric",
  month: "long",
  year: "numeric",
});
const shortDateFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  day: "numeric",
  month: "short",
});

function parse(value: string): Date | null {
  // Bare dates (week starts) are UTC midnight; timestamps carry their own zone.
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "27 November 2026" (UTC). */
export function formatDateUtc(value: string | null | undefined): string | null {
  const d = value ? parse(value) : null;
  return d ? dateFormat.format(d) : null;
}

/** "27 Nov" (UTC), for chart labels. */
export function formatShortDateUtc(value: string): string {
  const d = parse(value);
  return d ? shortDateFormat.format(d) : value;
}

/** Interval between full crawls in words: "every day", "every 2 days", "every 12 hours". */
export function describeInterval(hours: number): string {
  if (hours > 0 && hours % 24 === 0) {
    const days = hours / 24;
    return days === 1 ? "every day" : `every ${days} days`;
  }
  return hours === 1 ? "every hour" : `every ${hours} hours`;
}

/**
 * Full weeks far below the usual count (under a quarter of the median), such as
 * a week when processing slowed. Weeks before the feed reached its normal level
 * are not dips, so counting starts at the first normal week.
 */
export function lowWeeks(weeks: WeeklyActivity[]): WeeklyActivity[] {
  const full = weeks.filter((w) => !w.partial);
  if (full.length < 4) return [];
  const sorted = full.map((w) => w.new_papers).sort((a, b) => a - b);
  const threshold = sorted[Math.floor(sorted.length / 2)] / 4;
  if (threshold <= 0) return [];
  const start = full.findIndex((w) => w.new_papers >= threshold);
  return full.slice(start).filter((w) => w.new_papers < threshold);
}
