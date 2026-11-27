/**
 * Make NextAuth build its OAuth URLs from the host the visitor is actually on.
 *
 * NextAuth v4 uses NEXTAUTH_URL, whenever it is set, as the origin for the
 * Google redirect_uri and for every redirect after sign-in, regardless of
 * the request's host. The app is served on more than one host
 * (www.econnewsfeed.org and econ-newsfeed.vercel.app). A sign-in started on
 * one host therefore returned from Google to the other, where the
 * state/PKCE cookies (host-only) were missing. NextAuth then answered
 * `error=OAuthCallback`, and any session cookie it did set landed on the
 * wrong host.
 *
 * On Vercel, NextAuth trusts the forwarded host when NEXTAUTH_URL is unset
 * (Vercel only routes the project's own domains to it). Dropping
 * NEXTAUTH_URL there keeps the whole round trip on one host. Each host's
 * callback URL must still be registered in the Google OAuth client.
 * Self-hosted deployments keep NEXTAUTH_URL because their Host header is not
 * vouched for by a platform.
 *
 * Returns the NEXTAUTH_URL value that was dropped, if any.
 */
export function preferRequestHostForAuth(
  env: NodeJS.ProcessEnv = process.env,
): string | undefined {
  if (!env.VERCEL || !env.NEXTAUTH_URL) return undefined;
  const dropped = env.NEXTAUTH_URL;
  delete env.NEXTAUTH_URL;
  return dropped;
}
