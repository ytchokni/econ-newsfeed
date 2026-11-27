import { Suspense } from "react";
import type { Metadata } from "next";
import ResearchersContent from "./ResearchersContent";
import { openGraphFor, twitterFor } from "@/lib/seo";

const title = "Economists we track";
const description =
  "Search the economists whose personal websites Econ Newsfeed monitors, with their latest working papers and publications.";

export const metadata: Metadata = {
  title,
  description,
  // Search and filter variants point back to the plain directory.
  alternates: { canonical: "/researchers" },
  openGraph: openGraphFor({ type: "website", url: "/researchers", title, description }),
  twitter: twitterFor(title, description),
};

export default function ResearchersPage() {
  return (
    <div className="max-w-[800px] mx-auto px-6 py-8">
      <h1 className="text-2xl font-bold text-[var(--ink)] mb-8">
        Tracked Researchers
      </h1>
      <Suspense>
        <ResearchersContent />
      </Suspense>
    </div>
  );
}
