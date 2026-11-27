import type { Metadata } from "next";
import "./globals.css";
import { fontVariables } from "./font-variables";
import Header from "@/components/Header";
import { AuthProvider } from "@/lib/auth";
import {
  SITE_DESCRIPTION,
  SITE_NAME,
  getCanonicalOrigin,
  isIndexableDeployment,
  openGraphFor,
  twitterFor,
} from "@/lib/seo";

const indexable = isIndexableDeployment();

export const metadata: Metadata = {
  metadataBase: new URL(getCanonicalOrigin()),
  title: { default: SITE_NAME, template: `%s | ${SITE_NAME}` },
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  // Canonical URLs are set per page: a layout-level canonical would be
  // inherited by every page that forgot its own.
  robots: indexable
    ? { index: true, follow: true, googleBot: { index: true, follow: true, "max-snippet": -1, "max-image-preview": "large" } }
    : { index: false, follow: false },
  openGraph: openGraphFor({ type: "website", url: "/", title: SITE_NAME, description: SITE_DESCRIPTION }),
  twitter: twitterFor(SITE_NAME, SITE_DESCRIPTION),
  // Optional meta-tag verification for Google Search Console / Bing
  // Webmaster Tools (DNS TXT verification needs no code).
  verification: {
    google: process.env.GOOGLE_SITE_VERIFICATION || undefined,
    other: process.env.BING_SITE_VERIFICATION
      ? { "msvalidate.01": process.env.BING_SITE_VERIFICATION }
      : undefined,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={fontVariables}>
      <head>
        <link rel="alternate" type="application/atom+xml" title="Econ Newsfeed" href="/api/feed.xml" />
      </head>
      <body className="antialiased">
        <AuthProvider>
          <Header />
          <main>{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
