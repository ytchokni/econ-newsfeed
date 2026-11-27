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
