/** @jest-environment node */
import { isValidElement, type ReactElement } from "react";

jest.mock("../app/NewsfeedContent", () => ({ __esModule: true, default: () => null }));

const feed = { items: [], total: 0, page: 1, per_page: 20, pages: 0 };

function fallbackOf(tree: unknown): Record<string, unknown> {
  const children = (tree as ReactElement<{ children: ReactElement[] }>).props.children;
  const swr = children.find(
    (c) => isValidElement(c) && (c.props as { value?: { fallback?: unknown } }).value?.fallback !== undefined,
  ) as ReactElement<{ value: { fallback: Record<string, unknown> } }>;
  return swr.props.value.fallback;
}

beforeEach(() => jest.resetModules());

it("pre-fills page 1 of the default feed when there are no query parameters", async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => feed });
  const { default: Home } = await import("../app/page");
  const { defaultFeedUrl } = await import("../lib/feed-params");
  const tree = await Home({ searchParams: Promise.resolve({}) });
  expect(fallbackOf(tree)).toEqual({ [defaultFeedUrl()]: feed });
  expect(global.fetch).toHaveBeenCalledWith(
    `http://localhost:8000${defaultFeedUrl()}`,
    expect.any(Object),
  );
});

it("leaves filtered views to the browser", async () => {
  global.fetch = jest.fn();
  const { default: Home } = await import("../app/page");
  const tree = await Home({ searchParams: Promise.resolve({ tab: "all" }) });
  expect(fallbackOf(tree)).toEqual({});
  expect(global.fetch).not.toHaveBeenCalled();
});

it("still renders when the backend fails", async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
  const { default: Home } = await import("../app/page");
  const tree = await Home({ searchParams: Promise.resolve({}) });
  expect(fallbackOf(tree)).toEqual({});
});
