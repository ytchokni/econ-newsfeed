/** @jest-environment node */
jest.mock("../app/researchers/[id]/ResearcherDetailContent", () => ({
  __esModule: true, default: () => null,
}));
jest.mock("../app/papers/[id]/PaperDetailContent", () => ({
  __esModule: true, default: () => null,
}));

const originalApiUrl = process.env.API_INTERNAL_URL;

beforeEach(() => {
  jest.resetModules();
  delete process.env.API_INTERNAL_URL;
  global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 42 }) });
});

afterEach(() => {
  if (originalApiUrl === undefined) delete process.env.API_INTERNAL_URL;
  else process.env.API_INTERNAL_URL = originalApiUrl;
});

it("loads a researcher detail from the local backend without an API URL override", async () => {
  const { default: Page } = await import("../app/researchers/[id]/page");
  await Page({ params: Promise.resolve({ id: "42" }) });
  expect(global.fetch).toHaveBeenCalledWith(
    "http://localhost:8000/api/researchers/42", expect.any(Object),
  );
});

it("loads a paper detail from the local backend without an API URL override", async () => {
  const { default: Page } = await import("../app/papers/[id]/page");
  await Page({ params: Promise.resolve({ id: "42" }) });
  expect(global.fetch).toHaveBeenCalledWith(
    "http://localhost:8000/api/publications/42?include_history=true", expect.any(Object),
  );
});
