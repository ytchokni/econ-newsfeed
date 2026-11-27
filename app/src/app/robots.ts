import type { MetadataRoute } from "next";
import { getCanonicalOrigin, isIndexableDeployment } from "@/lib/seo";

/**
 * Crawlers that collect pages for AI training or bulk datasets, plus
 * assistant fetchers the owner chose to refuse. Search engines (Googlebot,
 * Bingbot, …) are unaffected: Google-Extended and Applebot-Extended are
 * control tokens only, so blocking them keeps pages in Google/Apple search
 * while opting out of AI training. AI *search* crawlers (OAI-SearchBot,
 * Claude-SearchBot) are deliberately not listed; add them here to opt out of
 * AI answer engines too. robots.txt is advisory: it only stops crawlers that
 * honour it.
 */
export const BLOCKED_AGENTS = [
  // OpenAI
  "GPTBot",
  "ChatGPT-User",
  // Anthropic (anthropic-ai and Claude-Web are legacy tokens)
  "ClaudeBot",
  "Claude-User",
  "anthropic-ai",
  "Claude-Web",
  // Google / Apple AI-training controls (search indexing unaffected)
  "Google-Extended",
  "Applebot-Extended",
  // Datasets and other AI crawlers
  "CCBot",
  "PerplexityBot",
  "Perplexity-User",
  "Bytespider",
  "Amazonbot",
  "meta-externalagent",
  "Meta-ExternalFetcher",
  "FacebookBot",
  "Diffbot",
  "omgili",
  "omgilibot",
  "Webzio-Extended",
  "ImagesiftBot",
  "cohere-ai",
  "cohere-training-data-crawler",
  "AI2Bot",
  "Ai2Bot-Dolma",
  "Timpibot",
  "img2dataset",
];

export function buildRobots(env: NodeJS.ProcessEnv = process.env): MetadataRoute.Robots {
  if (!isIndexableDeployment(env)) {
    // Preview / development deployments on Vercel: keep crawlers out entirely.
    return { rules: { userAgent: "*", disallow: "/" } };
  }
  const origin = getCanonicalOrigin(env);
  return {
    rules: [
      // AI-training and bulk crawlers: nothing.
      { userAgent: BLOCKED_AGENTS, disallow: "/" },
      // Everyone else, including search engines.
      {
        userAgent: "*",
        // Longest match wins, so these stay crawlable despite "/*?" below.
        allow: ["/", "/_next/", "/og-image.png"],
        disallow: [
          "/admin",
          "/api/",
          "/settings",
          "/unsubscribe",
          // Filter, search and pagination variants of the feed and directory.
          "/*?",
        ],
      },
    ],
    sitemap: `${origin}/sitemap.xml`,
    host: origin,
  };
}

export default function robots(): MetadataRoute.Robots {
  return buildRobots();
}
