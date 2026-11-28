import {
  describeInterval,
  formatCount,
  formatDateUtc,
  formatPct,
  formatShortDateUtc,
  lowWeeks,
} from "../site-stats";
import { niceMax } from "@/components/WeeklyActivityChart";

describe("site-stats formatting", () => {
  it("formats counts and percentages", () => {
    expect(formatCount(259794)).toBe("259,794");
    expect(formatPct(70.8)).toBe("70.8%");
    expect(formatPct(100)).toBe("100.0%");
    expect(formatPct(null)).toBe("–");
  });

  it("formats dates in UTC", () => {
    expect(formatDateUtc("2026-11-27T23:59:00Z")).toBe("27 November 2026");
    expect(formatDateUtc("2026-11-28T00:30:00+02:00")).toBe("27 November 2026");
    expect(formatDateUtc(null)).toBeNull();
    expect(formatDateUtc("not a date")).toBeNull();
    expect(formatShortDateUtc("2026-11-23")).toBe("23 Nov");
  });

  it("describes the crawl interval", () => {
    expect(describeInterval(24)).toBe("every day");
    expect(describeInterval(48)).toBe("every 2 days");
    expect(describeInterval(12)).toBe("every 12 hours");
    expect(describeInterval(1)).toBe("every hour");
  });

  it("finds dip weeks after the ramp-up, ignoring the current week", () => {
    const w = (week_start: string, new_papers: number, partial = false) => ({
      week_start, partial, new_papers, status_changes: 0, events: new_papers,
    });
    const weeks = [w("2026-09-07", 0), w("2026-09-14", 2), w("2026-09-21", 752), w("2026-09-28", 250),
      w("2026-10-05", 208), w("2026-10-12", 337), w("2026-10-19", 396), w("2026-10-26", 440),
      w("2026-11-02", 396), w("2026-11-09", 46), w("2026-11-16", 834), w("2026-11-23", 5, true)];
    expect(lowWeeks(weeks).map((x) => x.week_start)).toEqual(["2026-11-09"]);
    expect(lowWeeks(weeks.slice(2, 9))).toEqual([]);
    expect(lowWeeks(weeks.slice(0, 3))).toEqual([]); // too few weeks to judge
  });

  it("rounds chart maxima to readable steps", () => {
    expect(niceMax(0)).toBe(1);
    expect(niceMax(7)).toBe(10);
    expect(niceMax(1234)).toBe(2000);
    expect(niceMax(450)).toBe(500);
    expect(niceMax(2000)).toBe(2000);
  });
});
