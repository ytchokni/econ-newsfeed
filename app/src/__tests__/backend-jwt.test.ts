import { createHmac } from "crypto";
import { encodeBackendJwt } from "@/lib/backend-jwt";

function decodePart(part: string) {
  return JSON.parse(Buffer.from(part, "base64url").toString("utf8"));
}

describe("encodeBackendJwt", () => {
  it("creates a plain HS256 JWT the FastAPI backend can verify", () => {
    const token = encodeBackendJwt(
      { sub: "google-123", email: "test@example.com", name: "Test User" },
      "shared-secret",
    );

    const parts = token.split(".");
    expect(parts).toHaveLength(3);
    expect(decodePart(parts[0])).toEqual({ alg: "HS256", typ: "JWT" });
    expect(decodePart(parts[1])).toMatchObject({
      sub: "google-123",
      email: "test@example.com",
      name: "Test User",
    });

    const expectedSignature = createHmac("sha256", "shared-secret")
      .update(`${parts[0]}.${parts[1]}`)
      .digest("base64url");
    expect(parts[2]).toBe(expectedSignature);
  });
});
