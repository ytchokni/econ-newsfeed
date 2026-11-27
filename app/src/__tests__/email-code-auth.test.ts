/**
 * @jest-environment node
 */
import { authorizeEmailCode, clientIp } from "@/lib/email-code-auth";

const fetchMock = jest.fn();
const KEY = "test-internal-key-0123456789";
const originalKey = process.env.INTERNAL_API_KEY;

beforeEach(() => {
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
  process.env.INTERNAL_API_KEY = KEY;
});

afterEach(() => {
  if (originalKey === undefined) delete process.env.INTERNAL_API_KEY;
  else process.env.INTERNAL_API_KEY = originalKey;
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

it("returns a user:<id> subject from the backend account", async () => {
  fetchMock.mockResolvedValueOnce(
    jsonResponse(200, { id: 42, email: "a@example.com", name: null, picture_url: null }),
  );
  const user = await authorizeEmailCode(
    { email: " a@example.com ", code: "123456" },
    { "x-forwarded-for": "198.51.100.7, 10.0.0.1" },
    "http://backend.test",
  );

  expect(user).toEqual({ id: "user:42", email: "a@example.com", name: null, image: null });
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://backend.test/api/email-login/verify-code");
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body)).toEqual({ email: "a@example.com", code: "123456" });
  // The backend trusts the visitor IP only alongside the internal key.
  expect(init.headers["X-Internal-Api-Key"]).toBe(KEY);
  expect(init.headers["X-Visitor-IP"]).toBe("198.51.100.7");
  expect(init.headers["X-Visitor-Source"]).toBe("proxy");
  expect(init.headers["X-Real-IP"]).toBeUndefined();
});

it("sends no visitor headers when INTERNAL_API_KEY is unset", async () => {
  delete process.env.INTERNAL_API_KEY;
  fetchMock.mockResolvedValueOnce(
    jsonResponse(200, { id: 1, email: "a@example.com", name: null, picture_url: null }),
  );
  await authorizeEmailCode({ email: "a@example.com", code: "123456" }, { "x-real-ip": "203.0.113.5" }, "http://backend.test");
  const headers = fetchMock.mock.calls[0][1].headers;
  expect(Object.keys(headers)).toEqual(["Content-Type"]);
});

it("throws the backend error code so the modal can explain it", async () => {
  fetchMock.mockResolvedValueOnce(
    jsonResponse(400, { error: { code: "too_many_attempts", message: "..." } }),
  );
  await expect(
    authorizeEmailCode({ email: "a@example.com", code: "000000" }, {}, "http://backend.test"),
  ).rejects.toThrow("too_many_attempts");
});

it("falls back to a generic error when the backend is unreachable", async () => {
  fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
  await expect(
    authorizeEmailCode({ email: "a@example.com", code: "000000" }, {}, "http://backend.test"),
  ).rejects.toThrow("verify_failed");
});

it("rejects missing credentials without calling the backend", async () => {
  await expect(authorizeEmailCode({ email: "", code: "" }, {}, "http://backend.test")).rejects.toThrow(
    "invalid_code",
  );
  expect(fetchMock).not.toHaveBeenCalled();
});

it("prefers x-real-ip for the client address", () => {
  expect(clientIp({ "x-real-ip": "203.0.113.5", "x-forwarded-for": "198.51.100.1" })).toBe("203.0.113.5");
  expect(clientIp({})).toBeUndefined();
});
