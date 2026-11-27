// Site owner details for the Impressum, privacy page and removal links.
// Server-only: reads env vars at request/build time.

/**
 * Fields only the site owner can fill in. They render as clearly marked
 * placeholders until replaced here. Do not guess them.
 */
export const SITE_OWNER = {
  name: "[TO BE COMPLETED BY SITE OWNER: full name]",
  street: "[TO BE COMPLETED BY SITE OWNER: street and number]",
  city: "[TO BE COMPLETED BY SITE OWNER: postcode and city]",
  country: "Germany",
  supervisoryAuthority:
    "[TO BE COMPLETED BY SITE OWNER: data protection authority of your federal state]",
} as const;

export const PRIVACY_LAST_UPDATED = "2026-11-27";

/** Public contact address from CONTACT_EMAIL, or null if not configured. */
export function getContactEmail(): string | null {
  const value = (process.env.CONTACT_EMAIL || "").trim();
  return /^[^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]+$/.test(value) ? value : null;
}

/** Absolute origin of the public site, for links in mailto bodies. */
export function getSiteUrl(): string {
  const explicit = process.env.SITE_URL || process.env.NEXTAUTH_URL || process.env.AUTH_URL;
  if (explicit) return explicit.replace(/\/+$/, "");
  const vercel = process.env.VERCEL_PROJECT_PRODUCTION_URL;
  if (vercel) return `https://${vercel.replace(/\/+$/, "")}`;
  return "http://localhost:3000";
}
