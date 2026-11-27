// XML sitemap building. The backend supplies ids and last-modified times in
// chunks (/api/sitemap, /api/sitemap/{kind}?page=N); /sitemap.xml is an index
// pointing at /sitemaps/<file>.xml, each well under the 50,000-URL limit.

import { absoluteUrl } from "./seo";

export type SitemapKind = "researchers" | "papers";
export const SITEMAP_KINDS: SitemapKind[] = ["researchers", "papers"];

export interface SitemapIndexData {
  page_size: number;
  researchers: { count: number; pages: number };
  papers: { count: number; pages: number };
}

export interface SitemapChunk {
  items: { id: number; lastmod: string | null }[];
}

export interface SitemapEntry {
  loc: string;
  lastmod?: string | null;
}

/** Pages listed in every sitemap besides researchers and papers. */
export const STATIC_PATHS = ["/", "/researchers"];

export const SITEMAP_HEADERS = {
  "Content-Type": "application/xml; charset=utf-8",
  "Cache-Control": "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400",
  "X-Robots-Tag": "noindex",
};

export function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function entryXml(tag: "url" | "sitemap", e: SitemapEntry): string {
  const lastmod = e.lastmod ? `<lastmod>${xmlEscape(e.lastmod)}</lastmod>` : "";
  return `<${tag}><loc>${xmlEscape(e.loc)}</loc>${lastmod}</${tag}>`;
}

const XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n';
const NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"';

export function buildUrlset(entries: SitemapEntry[]): string {
  return `${XML_HEAD}<urlset ${NS}>\n${entries.map((e) => entryXml("url", e)).join("\n")}\n</urlset>\n`;
}

export function buildSitemapIndex(entries: SitemapEntry[]): string {
  return `${XML_HEAD}<sitemapindex ${NS}>\n${entries.map((e) => entryXml("sitemap", e)).join("\n")}\n</sitemapindex>\n`;
}

/** File names under /sitemaps/ for the given backend counts. */
export function sitemapFiles(index: SitemapIndexData): string[] {
  const files = ["pages.xml"];
  for (const kind of SITEMAP_KINDS) {
    for (let page = 0; page < index[kind].pages; page++) files.push(`${kind}-${page}.xml`);
  }
  return files;
}

export type ParsedSitemapFile = { kind: "pages" } | { kind: SitemapKind; page: number };

export function parseSitemapFile(file: string): ParsedSitemapFile | null {
  if (file === "pages.xml") return { kind: "pages" };
  const m = /^(researchers|papers)-(0|[1-9]\d{0,3})\.xml$/.exec(file);
  return m ? { kind: m[1] as SitemapKind, page: Number(m[2]) } : null;
}

export function chunkEntries(kind: SitemapKind, chunk: SitemapChunk, origin?: string): SitemapEntry[] {
  return chunk.items.map((item) => ({
    loc: absoluteUrl(`/${kind}/${item.id}`, origin),
    lastmod: item.lastmod,
  }));
}
