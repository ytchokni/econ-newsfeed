/**
 * @jest-environment node
 */
import { authorizeEmailCode, clientIp } from "@/lib/email-code-auth";

const fetchMock = jest.fn();

beforeEach(() => {
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
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
  expect(init.headers["X-Real-IP"]).toBe("198.51.100.7");
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
