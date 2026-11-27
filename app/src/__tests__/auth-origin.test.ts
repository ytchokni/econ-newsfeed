/**
 * @jest-environment node
 *
 * Google sign-in must return to the host it started on. With NEXTAUTH_URL
 * pinned to econ-newsfeed.vercel.app, a sign-in from www.econnewsfeed.org
 * came back to the vercel.app callback without its state cookie and failed
 * with error=OAuthCallback.
 */
import { join } from "path";
import { preferRequestHostForAuth } from "@/lib/auth-origin";

// NextAuth's own origin resolver; it is not in the package's export map.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { detectOrigin } = require(
  join(__dirname, "../../node_modules/next-auth/utils/detect-origin.js"),
) as { detectOrigin: (host: string, protocol: string) => string | undefined };

const STALE = "https://econ-newsfeed.vercel.app";

describe("preferRequestHostForAuth", () => {
  it("drops NEXTAUTH_URL on Vercel", () => {
    const env = { VERCEL: "1", NEXTAUTH_URL: STALE } as NodeJS.ProcessEnv;
    expect(preferRequestHostForAuth(env)).toBe(STALE);
    expect(env.NEXTAUTH_URL).toBeUndefined();
  });

  it("keeps NEXTAUTH_URL when self-hosted", () => {
    const env = { NEXTAUTH_URL: "http://localhost:3000" } as NodeJS.ProcessEnv;
    expect(preferRequestHostForAuth(env)).toBeUndefined();
    expect(env.NEXTAUTH_URL).toBe("http://localhost:3000");
  });

  it("is a no-op on Vercel without NEXTAUTH_URL", () => {
    const env = { VERCEL: "1" } as NodeJS.ProcessEnv;
    expect(preferRequestHostForAuth(env)).toBeUndefined();
    expect(env).toEqual({ VERCEL: "1" });
  });
});

describe("NextAuth origin on Vercel", () => {
  const saved = { ...process.env };
  afterEach(() => {
    process.env = { ...saved };
  });

  it.each(["www.econnewsfeed.org", "econ-newsfeed.vercel.app"])(
    "follows the request host %s once NEXTAUTH_URL is dropped",
    (host) => {
      process.env.VERCEL = "1";
      process.env.NEXTAUTH_URL = STALE;
      // Without the fix NextAuth ignores the host entirely.
      expect(detectOrigin("www.econnewsfeed.org", "https")).toBe(STALE);

      preferRequestHostForAuth();
      expect(detectOrigin(host, "https")).toBe(`https://${host}`);
    },
  );
});

describe("auth route module", () => {
  const saved = { ...process.env };
  afterEach(() => {
    process.env = { ...saved };
    jest.resetModules();
  });

  it("drops a pinned NEXTAUTH_URL on Vercel before NextAuth handles requests", () => {
    process.env.VERCEL = "1";
    process.env.NEXTAUTH_URL = STALE;
    jest.isolateModules(() => {
      jest.doMock("next-auth", () => ({ __esModule: true, default: () => () => null }));
      jest.doMock("next-auth/providers/google", () => ({ __esModule: true, default: () => ({}) }));
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      require("@/app/api/auth/[...nextauth]/route");
    });
    expect(process.env.NEXTAUTH_URL).toBeUndefined();
  });
});
