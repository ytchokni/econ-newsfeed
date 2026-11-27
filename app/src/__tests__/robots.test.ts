/** @jest-environment node */
import { BLOCKED_AGENTS, buildRobots } from "../app/robots";

const env = (vars: Record<string, string>) => vars as unknown as NodeJS.ProcessEnv;

describe("robots.txt", () => {
  it("lets crawlers in on production, with the sitemap on www", () => {
    const robots = buildRobots(env({ VERCEL_ENV: "production" }));
    expect(robots.sitemap).toBe("https://www.econnewsfeed.org/sitemap.xml");
    const rules = (Array.isArray(robots.rules) ? robots.rules : [robots.rules]).find((r) => r.userAgent === "*")!;
    expect(rules.allow).toEqual(expect.arrayContaining(["/", "/_next/", "/og-image.png"]));
    expect(rules.disallow).toEqual(
      expect.arrayContaining(["/admin", "/api/", "/settings", "/*?"]),
    );
  });

  it("shuts out AI-training and bulk crawlers but not search engines", () => {
    const robots = buildRobots(env({ VERCEL_ENV: "production" }));
    const rules = Array.isArray(robots.rules) ? robots.rules : [robots.rules];
    const blocked = rules.find((r) => Array.isArray(r.userAgent))!;
    expect(blocked.disallow).toBe("/");
    for (const agent of [
      "GPTBot", "ChatGPT-User", "CCBot", "ClaudeBot", "anthropic-ai", "Google-Extended",
      "Applebot-Extended", "PerplexityBot", "Bytespider", "Amazonbot", "meta-externalagent",
      "Diffbot", "omgili", "ImagesiftBot",
    ]) {
      expect(blocked.userAgent).toContain(agent);
    }
    for (const searchEngine of ["Googlebot", "Bingbot", "Applebot", "DuckDuckBot", "*"]) {
      expect(BLOCKED_AGENTS).not.toContain(searchEngine);
    }
  });

  it("follows SITE_URL for the sitemap link", () => {
    expect(buildRobots(env({ SITE_URL: "https://example.org" })).sitemap).toBe("https://example.org/sitemap.xml");
  });

  it("blocks everything on preview deployments", () => {
    const robots = buildRobots(env({ VERCEL_ENV: "preview" }));
    expect(robots.rules).toEqual({ userAgent: "*", disallow: "/" });
    expect(robots.sitemap).toBeUndefined();
  });
});
