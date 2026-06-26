import { createHmac } from "crypto";
import type { JWT } from "next-auth/jwt";

const THIRTY_DAYS_SECONDS = 30 * 24 * 60 * 60;

function base64UrlJson(value: unknown): string {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

export function encodeBackendJwt(token: JWT, secret: string): string {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    ...token,
    iat: typeof token.iat === "number" ? token.iat : now,
    exp: typeof token.exp === "number" ? token.exp : now + THIRTY_DAYS_SECONDS,
  };
  const header = { alg: "HS256", typ: "JWT" };
  const signingInput = `${base64UrlJson(header)}.${base64UrlJson(payload)}`;
  const signature = createHmac("sha256", secret)
    .update(signingInput)
    .digest("base64url");
  return `${signingInput}.${signature}`;
}
