import {
  buildPublicationsUrl,
  defaultFeedUrl,
  filtersFromParams,
  mergeTabFilters,
  parseTab,
} from "@/lib/feed-params";

it("describes the default feed a visitor without query parameters sees", () => {
  expect(defaultFeedUrl()).toBe(
    "/api/publications?page=1&per_page=20&status=revise_and_resubmit%2Caccepted%2Cpublished" +
      "&preset=top5_and_field&event_type=status_change",
  );
});

it("matches what the feed component requests for an empty URL", () => {
  const params = new URLSearchParams();
  const url = buildPublicationsUrl(1, 20, mergeTabFilters(filtersFromParams(params), parseTab(params.get("tab"))));
  expect(url).toBe(defaultFeedUrl());
});

it("keeps explicit tabs free of the default preset", () => {
  expect(filtersFromParams(new URLSearchParams("tab=all"))).toEqual({});
  expect(mergeTabFilters({}, "all")).toEqual({});
});
