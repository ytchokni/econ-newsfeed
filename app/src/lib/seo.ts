// Search-engine metadata: canonical URLs, titles, descriptions and JSON-LD.
// Pure functions so they can be unit tested without rendering pages.

import type { Metadata } from "next";
import { statusPillConfig } from "./publication-utils";
import type { Author, PublicationDetail, ResearcherDetail } from "./types";

export const SITE_NAME = "Econ Newsfeed";
export const DEFAULT_SITE_URL = "https://www.econnewsfeed.org";
export const SITE_DESCRIPTION =
  "New working papers, publications and status changes from economists' personal websites, the day they appear.";
export const OG_IMAGE_PATH = "/og-image.png";
export const DESCRIPTION_MAX = 160;

/** Canonical origin (no trailing slash). Override with SITE_URL. */
export function getCanonicalOrigin(env: NodeJS.ProcessEnv = process.env): string {
  const raw = (env.SITE_URL || "").trim();
  if (raw) {
    try {
      return new URL(raw).origin;
    } catch {
      // fall through to the default
    }
  }
  return DEFAULT_SITE_URL;
}

/**
 * False on Vercel preview and development deployments so their URLs never
 * get indexed; true on the production deployment and outside Vercel.
 */
export function isIndexableDeployment(env: NodeJS.ProcessEnv = process.env): boolean {
  return !env.VERCEL_ENV || env.VERCEL_ENV === "production";
}

export function absoluteUrl(path: string, origin = getCanonicalOrigin()): string {
  return `${origin}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Shorten to at most `max` characters on a word boundary, adding an ellipsis. */
export function truncate(text: string | null | undefined, max: number): string {
  const clean = (text ?? "").replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max - 1);
  // Keep the last word if the cut falls exactly at its end.
  const space = clean[max - 1] === " " ? cut.length : cut.lastIndexOf(" ");
  const base = space > max * 0.6 ? cut.slice(0, space) : cut;
  return `${base.replace(/[\s,;:.\-–—]+$/, "")}…`;
}

const ogImages = [{ url: OG_IMAGE_PATH, width: 1200, height: 630, alt: SITE_NAME }];

/** Open Graph block with the site-wide fields every page needs. */
export function openGraphFor(
  og: NonNullable<Metadata["openGraph"]> & { url: string },
): Metadata["openGraph"] {
  return { siteName: SITE_NAME, locale: "en_US", images: ogImages, ...og };
}

export function twitterFor(title: string, description: string): Metadata["twitter"] {
  return { card: "summary_large_image", title, description, images: [OG_IMAGE_PATH] };
}

// --- Researchers -----------------------------------------------------------

export function researcherName(r: Pick<ResearcherDetail, "first_name" | "last_name">): string {
  return `${r.first_name} ${r.last_name}`.replace(/\s+/g, " ").trim();
}

export function researcherRole(r: Pick<ResearcherDetail, "position" | "affiliation">): string | null {
  if (r.position && r.affiliation) return `${r.position} at ${r.affiliation}`;
  return r.position || r.affiliation || null;
}

/**
 * Only profiles of researchers we track (have a website on file) and that
 * list at least one paper are indexed. Coauthor-only stubs and empty profiles
 * are thin pages. Keep in sync with backend/database/sitemap.py.
 */
export function isIndexableResearcher(r: Pick<ResearcherDetail, "urls" | "publications">): boolean {
  return r.urls.length > 0 && r.publications.length > 0;
}

export function researcherTitle(r: ResearcherDetail): string {
  const name = researcherName(r);
  return r.affiliation ? `${name} — ${r.affiliation}` : name;
}

const LATEST_LABEL = " Latest papers: ";

/** Appends quoted paper titles to `base` while the result fits in `max`. */
function withLatestTitles(base: string, titles: string[], max: number): string {
  let out = base;
  let added = 0;
  for (const title of titles) {
    const piece = `${added === 0 ? LATEST_LABEL : "; "}“${title}”`;
    if ((out + piece + ".").length > max) break;
    out += piece;
    added += 1;
  }
  return added > 0 ? `${out}.` : out;
}

export function researcherDescription(r: ResearcherDetail): string {
  const role = researcherRole(r);
  const intro = `${researcherName(r)}${role ? `, ${role}` : ""}`;
  const bio = (r.description || "").trim();
  const titles = r.publications.slice(0, 3).map((p) => p.title.trim()).filter(Boolean);
  if (titles.length > 0) {
    // A long bio is shortened to leave room for at least one latest-paper title.
    const base = bio ? truncate(bio, 100) : `${intro}.`;
    const withTitles = withLatestTitles(base, titles, DESCRIPTION_MAX);
    if (withTitles.includes(LATEST_LABEL)) return withTitles;
  }
  return truncate(
    bio || `${intro}: new and updated papers, tracked from their website by ${SITE_NAME}.`,
    DESCRIPTION_MAX,
  );
}

export function researcherMetadata(r: ResearcherDetail): Metadata {
  const title = researcherTitle(r);
  const description = researcherDescription(r);
  const path = `/researchers/${r.id}`;
  return {
    title,
    description,
    alternates: { canonical: path },
    // Omit the key for indexable profiles: an explicit undefined would drop
    // the layout's robots tag.
    ...(isIndexableResearcher(r) ? {} : { robots: { index: false, follow: true } }),
    openGraph: openGraphFor({
      type: "profile",
      url: path,
      title,
      description,
      firstName: r.first_name,
      lastName: r.last_name,
    }),
    twitter: twitterFor(title, description),
  };
}

function openAlexUrl(id: string | null | undefined, prefix: "A" | "W"): string | null {
  if (!id) return null;
  const bare = id.replace(/^https?:\/\/openalex\.org\//i, "");
  return new RegExp(`^${prefix}\\d+$`, "i").test(bare) ? `https://openalex.org/${bare.toUpperCase()}` : null;
}

function httpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

/** schema.org ProfilePage with the researcher as its Person. */
export function researcherJsonLd(r: ResearcherDetail, origin = getCanonicalOrigin()) {
  const url = absoluteUrl(`/researchers/${r.id}`, origin);
  const sameAs = [httpUrl(r.website_url), openAlexUrl(r.openalex_author_id, "A")].filter(
    (v): v is string => Boolean(v),
  );
  const person: Record<string, unknown> = {
    "@type": "Person",
    "@id": `${url}#person`,
    name: researcherName(r),
    givenName: r.first_name,
    familyName: r.last_name,
    url,
  };
  if (r.position) person.jobTitle = r.position;
  if (r.affiliation) person.affiliation = { "@type": "Organization", name: r.affiliation };
  if (r.description) person.description = truncate(r.description, 500);
  if (sameAs.length) person.sameAs = sameAs;
  if (r.fields?.length) person.knowsAbout = r.fields.map((f) => f.name);
  return {
    "@context": "https://schema.org",
    "@type": "ProfilePage",
    url,
    name: `${researcherName(r)} | ${SITE_NAME}`,
    mainEntity: person,
  };
}

// --- Papers ----------------------------------------------------------------

export function authorsShort(authors: Pick<Author, "first_name" | "last_name">[]): string {
  if (authors.length === 0) return "";
  if (authors.length === 1) return researcherName(authors[0]);
  if (authors.length === 2) return `${authors[0].last_name} & ${authors[1].last_name}`;
  return `${authors[0].last_name} et al.`;
}

export function paperTitle(p: PublicationDetail): string {
  const by = authorsShort(p.authors);
  return by ? `${p.title} — ${by}` : p.title;
}

/** Abstract text without a leading "Abstract:" label scraped along with it. */
export function cleanAbstract(abstract: string | null | undefined): string {
  return (abstract || "").replace(/^\s*abstract\b\s*[:.\-–—]?\s*/i, "").trim();
}

export function paperDescription(p: PublicationDetail): string {
  const abstract = cleanAbstract(p.abstract);
  if (abstract) return truncate(abstract, DESCRIPTION_MAX);
  const kind = p.status ? statusPillConfig[p.status]?.label ?? "Paper" : "Paper";
  const names = p.authors.map(researcherName).join(", ");
  const where = [p.venue, p.year].filter(Boolean).join(", ");
  const text = `${kind}${names ? ` by ${names}` : ""}${where ? ` (${where})` : ""}: “${p.title}”.`;
  return truncate(text, DESCRIPTION_MAX);
}

export function paperMetadata(p: PublicationDetail): Metadata {
  const title = paperTitle(p);
  const description = paperDescription(p);
  const path = `/papers/${p.id}`;
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: openGraphFor({
      type: "article",
      url: path,
      title: p.title,
      description,
      authors: p.authors.map(researcherName),
    }),
    twitter: twitterFor(p.title, description),
  };
}

const PUBLISHED_STATUSES = new Set(["published", "accepted"]);

/** schema.org ScholarlyArticle for a paper page. */
export function paperJsonLd(p: PublicationDetail, origin = getCanonicalOrigin()) {
  const url = absoluteUrl(`/papers/${p.id}`, origin);
  const article: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "ScholarlyArticle",
    "@id": `${url}#article`,
    url,
    mainEntityOfPage: url,
    name: p.title,
    headline: truncate(p.title, 110),
    author: (p.authors ?? []).map((a) => ({
      "@type": "Person",
      name: researcherName(a),
      url: absoluteUrl(`/researchers/${a.id}`, origin),
    })),
  };
  if (p.year && /^\d{4}$/.test(p.year)) article.datePublished = p.year;
  const abstract = cleanAbstract(p.abstract);
  if (abstract) article.abstract = abstract;
  if (p.status) article.creativeWorkStatus = statusPillConfig[p.status]?.label ?? p.status;
  if (p.venue && p.status && PUBLISHED_STATUSES.has(p.status)) {
    article.isPartOf = { "@type": "Periodical", name: p.venue };
  }
  const sameAs: string[] = [];
  if (p.doi) {
    const doi = p.doi.replace(/^https?:\/\/(dx\.)?doi\.org\//i, "");
    article.identifier = { "@type": "PropertyValue", propertyID: "DOI", value: doi };
    sameAs.push(`https://doi.org/${doi}`);
  }
  const openalex = openAlexUrl(p.openalex_id, "W");
  if (openalex) sameAs.push(openalex);
  if (sameAs.length) article.sameAs = sameAs;
  return article;
}

// --- Site ------------------------------------------------------------------

export function websiteJsonLd(origin = getCanonicalOrigin()) {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "@id": `${origin}/#website`,
    name: SITE_NAME,
    alternateName: "econnewsfeed.org",
    url: `${origin}/`,
    description: SITE_DESCRIPTION,
  };
}

/** JSON for a <script type="application/ld+json"> body; `<` is escaped so data can't close the tag. */
export function serializeJsonLd(data: unknown): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}
