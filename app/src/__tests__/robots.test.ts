/** @jest-environment node */
import { buildRobots } from "../app/robots";

const env = (vars: Record<string, string>) => vars as unknown as NodeJS.ProcessEnv;

describe("robots.txt", () => {
  it("lets crawlers in on production, with the sitemap on www", () => {
    const robots = buildRobots(env({ VERCEL_ENV: "production" }));
    expect(robots.sitemap).toBe("https://www.econnewsfeed.org/sitemap.xml");
    const rules = Array.isArray(robots.rules) ? robots.rules[0] : robots.rules;
    expect(rules.userAgent).toBe("*");
    expect(rules.allow).toEqual(expect.arrayContaining(["/", "/_next/", "/og-image.png"]));
    expect(rules.disallow).toEqual(
      expect.arrayContaining(["/admin", "/api/", "/settings", "/*?"]),
    );
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
