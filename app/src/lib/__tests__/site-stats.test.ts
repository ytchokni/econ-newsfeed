import {
  describeInterval,
  fieldsCoverageShare,
  formatCount,
  formatDateUtc,
  formatPct,
  formatShortDateUtc,
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

  it("computes the share of tracked researchers with field tags", () => {
    expect(fieldsCoverageShare({ researchers: { total: 10, tracked: 4, pages_tracked: 5, with_fields: 3 } })).toBe(0.75);
    expect(fieldsCoverageShare({ researchers: { total: 0, tracked: 0, pages_tracked: 0, with_fields: 0 } })).toBeNull();
  });

  it("rounds chart maxima to readable steps", () => {
    expect(niceMax(0)).toBe(1);
    expect(niceMax(7)).toBe(10);
    expect(niceMax(1234)).toBe(2000);
    expect(niceMax(450)).toBe(500);
    expect(niceMax(2000)).toBe(2000);
  });
});
