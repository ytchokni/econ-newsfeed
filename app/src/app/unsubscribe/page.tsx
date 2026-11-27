import type { Metadata } from "next";
import UnsubscribeContent from "./UnsubscribeContent";

export const metadata: Metadata = {
  title: "Unsubscribe — Econ Newsfeed",
  robots: "noindex, nofollow",
  // The URL carries the unsubscribe token; never leak it to linked sites.
  referrer: "no-referrer",
};

export default async function UnsubscribePage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string | string[] }>;
}) {
  const { token } = await searchParams;
  return <UnsubscribeContent token={typeof token === "string" ? token : ""} />;
}
