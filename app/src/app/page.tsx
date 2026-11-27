import { Suspense } from "react";
import type { Metadata } from "next";
import NewsfeedContent from "./NewsfeedContent";
import JsonLd from "@/components/JsonLd";
import { SITE_DESCRIPTION, SITE_NAME, openGraphFor, twitterFor, websiteJsonLd } from "@/lib/seo";

const title = `${SITE_NAME} — new economics papers from researchers' websites`;

export const metadata: Metadata = {
  title: { absolute: title },
  description: SITE_DESCRIPTION,
  // Filtered views (?status=…, ?search=…) all point back to the plain feed.
  alternates: { canonical: "/" },
  openGraph: openGraphFor({ type: "website", url: "/", title, description: SITE_DESCRIPTION }),
  twitter: twitterFor(title, SITE_DESCRIPTION),
};

export default function Home() {
  return (
    <>
      <JsonLd data={websiteJsonLd()} />
      <Suspense>
        <NewsfeedContent />
      </Suspense>
    </>
  );
}
