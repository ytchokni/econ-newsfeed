import { Suspense } from "react";
import type { Metadata } from "next";
import { SWRConfig } from "swr";
import NewsfeedContent from "./NewsfeedContent";
import JsonLd from "@/components/JsonLd";
import { defaultFeedUrl } from "@/lib/feed-params";
import { fetchBackend } from "@/lib/server-api";
import { SITE_DESCRIPTION, SITE_NAME, openGraphFor, twitterFor, websiteJsonLd } from "@/lib/seo";
import type { PaginatedResponse, Publication } from "@/lib/types";

const title = `${SITE_NAME} — new economics papers from researchers' websites`;

export const metadata: Metadata = {
  title: { absolute: title },
  description: SITE_DESCRIPTION,
  // Filtered views (?status=…, ?search=…) all point back to the plain feed.
  alternates: { canonical: "/" },
  openGraph: openGraphFor({ type: "website", url: "/", title, description: SITE_DESCRIPTION }),
  twitter: twitterFor(title, SITE_DESCRIPTION),
};

/**
 * First page of the default feed, fetched on the server so the HTML carries
 * the papers (for crawlers and a faster first paint). Filtered views load in
 * the browser as before. A backend failure just falls back to client loading.
 */
async function defaultFeedFallback(): Promise<Record<string, unknown>> {
  const url = defaultFeedUrl();
  try {
    const data = await fetchBackend<PaginatedResponse<Publication>>(url, 60);
    return data ? { [url]: data } : {};
  } catch {
    return {};
  }
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  // Reading searchParams renders this page per request, so the feed (which
  // reads the URL) is rendered on the server instead of only in the browser.
  const params = await searchParams;
  const fallback = Object.keys(params).length === 0 ? await defaultFeedFallback() : {};

  return (
    <>
      <JsonLd data={websiteJsonLd()} />
      <SWRConfig value={{ fallback }}>
        <Suspense>
          <NewsfeedContent />
        </Suspense>
      </SWRConfig>
    </>
  );
}
