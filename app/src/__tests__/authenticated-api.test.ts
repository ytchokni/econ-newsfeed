import useSWR from "swr";
import { followResearcher, unfollowResearcher, usePublications } from "@/lib/api";

jest.mock("swr", () => ({
  __esModule: true,
  default: jest.fn(),
}));

const mockedUseSWR = useSWR as jest.Mock;
const fetchMock = jest.fn();

beforeEach(() => {
  mockedUseSWR.mockReset();
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

function mockResponse(status: number, body?: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 204 ? "No Content" : "OK",
    json: jest.fn(async () => body),
  } as unknown as Response;
}

describe("authenticated API helpers", () => {
  it("does not parse empty follow responses as JSON", async () => {
    const response = mockResponse(204);
    fetchMock.mockResolvedValue(response);

    await expect(followResearcher(42, "token-123")).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/users/follow/42",
      expect.objectContaining({
        method: "POST",
        headers: { Authorization: "Bearer token-123" },
      }),
    );
    expect(response.json).not.toHaveBeenCalled();
  });

  it("does not parse empty unfollow responses as JSON", async () => {
    const response = mockResponse(204);
    fetchMock.mockResolvedValue(response);

    await expect(unfollowResearcher(42, "token-123")).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/users/follow/42",
      expect.objectContaining({
        method: "DELETE",
        headers: { Authorization: "Bearer token-123" },
      }),
    );
    expect(response.json).not.toHaveBeenCalled();
  });

  it("uses the bearer token when loading the following feed preset", async () => {
    mockedUseSWR.mockReturnValue({});
    usePublications(2, 20, { preset: "following", event_type: "new_paper" }, "token-123");

    const [key, fetcher] = mockedUseSWR.mock.calls[0];
    expect(key).toEqual([
      "/api/publications?page=2&per_page=20&preset=following&event_type=new_paper",
      "token-123",
    ]);

    fetchMock.mockResolvedValue(
      mockResponse(200, { items: [], total: 0, page: 2, per_page: 20, pages: 0 }),
    );
    await fetcher(key);

    expect(fetchMock).toHaveBeenCalledWith(
      key[0],
      expect.objectContaining({
        headers: { Authorization: "Bearer token-123" },
      }),
    );
  });
});
