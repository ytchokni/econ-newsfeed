/**
 * Server-side half of email-code login: NextAuth's credentials provider hands
 * the email and code to the backend, which checks and consumes the code and
 * returns the account. The session subject is `user:<id>`, which the backend
 * resolves by account id (Google sessions keep the Google subject).
 */

export const EMAIL_CODE_PROVIDER_ID = "email-code";

export interface EmailCodeUser {
  id: string;
  email: string;
  name: string | null;
  image: string | null;
}

type HeaderBag = Record<string, string | string[] | undefined> | undefined;

function firstHeader(headers: HeaderBag, name: string): string | undefined {
  const value = headers?.[name] ?? headers?.[name.toLowerCase()];
  const raw = Array.isArray(value) ? value[0] : value;
  return raw?.split(",")[0]?.trim() || undefined;
}

/** The visitor's address, so the backend rate-limits per visitor, not per Vercel function. */
export function clientIp(headers: HeaderBag): string | undefined {
  return firstHeader(headers, "x-real-ip") ?? firstHeader(headers, "x-forwarded-for");
}

/**
 * Exchange an emailed code for the account. Throws an Error whose message is
 * the backend's error code (e.g. `invalid_code`); NextAuth passes that to the
 * client as `signIn(...).error`.
 */
export async function authorizeEmailCode(
  credentials: Record<string, string> | undefined,
  headers?: HeaderBag,
  apiBase: string = process.env.API_INTERNAL_URL || "http://localhost:8000",
): Promise<EmailCodeUser> {
  const email = credentials?.email?.trim() ?? "";
  const code = credentials?.code?.trim() ?? "";
  if (!email || !code) throw new Error("invalid_code");

  const ip = clientIp(headers);
  let res: Response;
  try {
    res = await fetch(`${apiBase}/api/email-login/verify-code`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(ip ? { "X-Real-IP": ip } : {}),
      },
      body: JSON.stringify({ email, code }),
      cache: "no-store",
    });
  } catch {
    throw new Error("verify_failed");
  }

  if (!res.ok) {
    let errorCode = "verify_failed";
    try {
      const body = await res.json();
      if (typeof body?.error?.code === "string") errorCode = body.error.code;
    } catch {
      // Non-JSON error body: keep the generic code.
    }
    throw new Error(errorCode);
  }

  const user = await res.json();
  if (typeof user?.id !== "number") throw new Error("verify_failed");
  return {
    id: `user:${user.id}`,
    email: user.email,
    name: user.name ?? null,
    image: user.picture_url ?? null,
  };
}
