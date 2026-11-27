import { formatCount, formatShortDateUtc, type WeeklyActivity } from "@/lib/site-stats";

/** Round up to a 1/2/5 × 10^n step so the top gridline has a readable value. */
export function niceMax(value: number): number {
  if (value <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 5, 10]) {
    if (value <= step * pow) return step * pow;
  }
  return 10 * pow;
}

function weekLabel(w: WeeklyActivity): string {
  const base = `Week of ${formatShortDateUtc(w.week_start)}: ${formatCount(w.new_papers)} new papers, ${formatCount(w.status_changes)} status updates`;
  return w.partial ? `${base} (so far)` : base;
}

/**
 * New papers per week as plain HTML bars (server-rendered, no client JS), so
 * labels stay legible at phone width. One series: the caption names it.
 * Hovering a column shows the week's numbers; a table view is below.
 */
export default function WeeklyActivityChart({ weeks }: { weeks: WeeklyActivity[] }) {
  if (weeks.length === 0) return null;
  const max = niceMax(Math.max(...weeks.map((w) => w.new_papers)));
  const lastFull = weeks.reduce((last, w, i) => (w.partial ? last : i), -1);
  const showOnMobile = (i: number) => (weeks.length - 1 - i) % 3 === 0;

  return (
    <figure className="m-0">
      <figcaption className="text-sm font-semibold text-[var(--ink)] mb-3">
        New papers in the feed per week
        <span className="font-normal text-[var(--muted)]"> · last {weeks.length} weeks, UTC</span>
      </figcaption>
      <div className="flex gap-2" aria-hidden="true">
        {/* y-axis labels */}
        <div className="relative w-9 shrink-0 h-40 text-[10px] text-[var(--muted)] tabular-nums">
          <span className="absolute right-0 top-5 -translate-y-1/2">{formatCount(max)}</span>
          <span className="absolute right-0 top-[calc(1.25rem+(100%-1.25rem)/2)] -translate-y-1/2">
            {formatCount(max / 2)}
          </span>
          <span className="absolute right-0 bottom-0 translate-y-1/2">0</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="relative h-40 pt-5">
            {/* recessive gridlines */}
            <div className="absolute inset-x-0 top-5 border-t border-[var(--line)]" />
            <div className="absolute inset-x-0 top-[calc(1.25rem+(100%-1.25rem)/2)] border-t border-[var(--line)]" />
            <div className="relative flex items-end h-full gap-[2px] sm:gap-1.5 border-b border-[var(--line2)]">
              {weeks.map((w, i) => (
                <div
                  key={w.week_start}
                  title={weekLabel(w)}
                  data-testid="week-bar"
                  className="group relative flex-1 min-w-0 h-full flex flex-col justify-end items-center"
                >
                  {i === lastFull && (
                    <span className="absolute -top-5 text-[11px] font-semibold text-[var(--ink)] tabular-nums whitespace-nowrap">
                      {formatCount(w.new_papers)}
                    </span>
                  )}
                  <div
                    className={`w-full max-w-[34px] rounded-t-[4px] transition-opacity group-hover:opacity-80 ${
                      w.partial
                        ? "bg-[var(--accent)]/30 border border-b-0 border-dashed border-[var(--accent)]"
                        : "bg-[var(--accent)]"
                    }`}
                    style={{ height: `${(100 * w.new_papers) / max}%`, minHeight: w.new_papers > 0 ? 2 : 0 }}
                  />
                </div>
              ))}
            </div>
          </div>
          <div className="flex gap-[2px] sm:gap-1.5 mt-1.5 text-[10px] text-[var(--muted)]">
            {weeks.map((w, i) => (
              <span
                key={w.week_start}
                className={`flex-1 min-w-0 text-center whitespace-nowrap ${showOnMobile(i) ? "" : "invisible sm:visible"}`}
              >
                {w.partial ? "now" : formatShortDateUtc(w.week_start)}
              </span>
            ))}
          </div>
        </div>
      </div>
      <details className="mt-3 text-xs text-[var(--ink2)]">
        <summary className="cursor-pointer text-[var(--muted)] hover:text-[var(--ink)]">Show as table</summary>
        <table className="mt-2 w-full max-w-md border-collapse tabular-nums">
          <thead>
            <tr className="text-left text-[var(--muted)]">
              <th className="font-medium py-1 pr-4">Week of (UTC)</th>
              <th className="font-medium py-1 pr-4 text-right">New papers</th>
              <th className="font-medium py-1 text-right">Status updates</th>
            </tr>
          </thead>
          <tbody>
            {weeks.map((w) => (
              <tr key={w.week_start} className="border-t border-[var(--line)]">
                <td className="py-1 pr-4">
                  {formatShortDateUtc(w.week_start)}
                  {w.partial ? " (so far)" : ""}
                </td>
                <td className="py-1 pr-4 text-right">{formatCount(w.new_papers)}</td>
                <td className="py-1 text-right">{formatCount(w.status_changes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
