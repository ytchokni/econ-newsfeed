import {
  DEFAULT_SITE_URL,
  authorsShort,
  cleanAbstract,
  getCanonicalOrigin,
  isIndexableDeployment,
  isIndexableResearcher,
  paperDescription,
  paperJsonLd,
  paperMetadata,
  paperTitle,
  researcherDescription,
  researcherJsonLd,
  researcherMetadata,
  researcherTitle,
  serializeJsonLd,
  truncate,
  websiteJsonLd,
} from "@/lib/seo";
import type { Publication, PublicationDetail, ResearcherDetail } from "@/lib/types";

const ORIGIN = "https://www.econnewsfeed.org";

function pub(overrides: Partial<Publication> = {}): Publication {
  return {
    id: 10,
    title: "Immigration and Wages",
    authors: [{ id: 1, first_name: "Max", last_name: "Steinhardt", affiliation: null }],
    year: "2024",
    venue: null,
    source_url: null,
    discovered_at: "2026-03-15T14:30:00Z",
    status: "working_paper",
    abstract: null,
    draft_url: null,
    draft_url_status: "unchecked",
    draft_available: false,
    doi: null,
    coauthors: [],
    links: [],
    ...overrides,
  };
}

function researcher(overrides: Partial<ResearcherDetail> = {}): ResearcherDetail {
  return {
    id: 1,
    first_name: "Max",
    last_name: "Steinhardt",
    position: "Professor",
    affiliation: "Freie Universität Berlin",
    description: null,
    urls: [{ id: 5, page_type: "home", url: "https://max.example.org/" }],
    website_url: "https://max.example.org/",
    publication_count: 2,
    fields: [],
    jel_codes: [],
    openalex_author_id: "A5085299813",
    publications: [pub(), pub({ id: 11, title: "Trade Shocks" })],
    ...overrides,
  } as ResearcherDetail;
}

function paper(overrides: Partial<PublicationDetail> = {}): PublicationDetail {
  return {
    ...pub(),
    feed_events: [],
    history: [],
    is_seed: false,
    title_hash: null,
    openalex_id: null,
    ...overrides,
  };
}

describe("site settings", () => {
  it("defaults the canonical origin to www", () => {
    expect(getCanonicalOrigin({} as NodeJS.ProcessEnv)).toBe(DEFAULT_SITE_URL);
    expect(DEFAULT_SITE_URL).toBe(ORIGIN);
  });

  it("accepts SITE_URL and strips paths and trailing slashes", () => {
    expect(getCanonicalOrigin({ SITE_URL: "https://example.org/x/" } as unknown as NodeJS.ProcessEnv)).toBe(
      "https://example.org",
    );
    expect(getCanonicalOrigin({ SITE_URL: "not a url" } as unknown as NodeJS.ProcessEnv)).toBe(ORIGIN);
  });

  it("is indexable in production and outside Vercel only", () => {
    expect(isIndexableDeployment({} as NodeJS.ProcessEnv)).toBe(true);
    expect(isIndexableDeployment({ VERCEL_ENV: "production" } as unknown as NodeJS.ProcessEnv)).toBe(true);
    expect(isIndexableDeployment({ VERCEL_ENV: "preview" } as unknown as NodeJS.ProcessEnv)).toBe(false);
    expect(isIndexableDeployment({ VERCEL_ENV: "development" } as unknown as NodeJS.ProcessEnv)).toBe(false);
  });
});

describe("truncate", () => {
  it("leaves short text alone and collapses whitespace", () => {
    expect(truncate("  a   b ", 10)).toBe("a b");
  });

  it("cuts on a word boundary with an ellipsis", () => {
    const out = truncate("The quick brown fox jumps over the lazy dog", 20);
    expect(out.length).toBeLessThanOrEqual(20);
    expect(out).toBe("The quick brown fox…");
  });
});

describe("researcher metadata", () => {
  it("titles the page 'Name — affiliation'", () => {
    expect(researcherTitle(researcher())).toBe("Max Steinhardt — Freie Universität Berlin");
    expect(researcherTitle(researcher({ affiliation: null }))).toBe("Max Steinhardt");
  });

  it("builds the description from role and latest papers when there is no bio", () => {
    const d = researcherDescription(researcher());
    expect(d).toBe(
      "Max Steinhardt, Professor at Freie Universität Berlin. Latest papers: “Immigration and Wages”; “Trade Shocks”.",
    );
    expect(d.length).toBeLessThanOrEqual(160);
  });

  it("starts from the bio and keeps room for a paper title", () => {
    const bio = "Max Steinhardt is a labour economist working on migration. ".repeat(5);
    const d = researcherDescription(researcher({ description: bio }));
    expect(d.startsWith("Max Steinhardt is a labour economist")).toBe(true);
    expect(d).toContain("Latest papers: “Immigration and Wages”");
    expect(d.length).toBeLessThanOrEqual(160);
  });

  it("sets canonical, Open Graph profile and keeps indexable profiles indexable", () => {
    const m = researcherMetadata(researcher());
    expect(m.title).toBe("Max Steinhardt — Freie Universität Berlin");
    expect(m.alternates?.canonical).toBe("/researchers/1");
    expect(m).not.toHaveProperty("robots");
    expect(m.openGraph).toMatchObject({ type: "profile", url: "/researchers/1", siteName: "Econ Newsfeed" });
    expect((m.openGraph as { images: unknown[] }).images).toHaveLength(1);
    expect(m.twitter).toMatchObject({ card: "summary_large_image" });
  });

  it("falls back to a generic sentence without papers", () => {
    expect(researcherDescription(researcher({ publications: [], position: null }))).toBe(
      "Max Steinhardt, Freie Universität Berlin: new and updated papers, tracked from their website by Econ Newsfeed.",
    );
  });

  it("marks coauthor stubs and empty profiles noindex, follow", () => {
    expect(isIndexableResearcher(researcher({ urls: [] }))).toBe(false);
    expect(isIndexableResearcher(researcher({ publications: [] }))).toBe(false);
    expect(researcherMetadata(researcher({ urls: [] })).robots).toEqual({ index: false, follow: true });
  });
});

describe("researcher JSON-LD", () => {
  it("is a ProfilePage whose main entity is the Person", () => {
    const ld = researcherJsonLd(researcher({ description: "Labour economist." }), ORIGIN);
    expect(ld["@context"]).toBe("https://schema.org");
    expect(ld["@type"]).toBe("ProfilePage");
    expect(ld.url).toBe(`${ORIGIN}/researchers/1`);
    expect(ld.mainEntity).toEqual({
      "@type": "Person",
      "@id": `${ORIGIN}/researchers/1#person`,
      name: "Max Steinhardt",
      givenName: "Max",
      familyName: "Steinhardt",
      url: `${ORIGIN}/researchers/1`,
      jobTitle: "Professor",
      affiliation: { "@type": "Organization", name: "Freie Universität Berlin" },
      description: "Labour economist.",
      sameAs: ["https://max.example.org/", "https://openalex.org/A5085299813"],
    });
  });

  it("omits missing fields and unsafe links", () => {
    const ld = researcherJsonLd(
      researcher({ position: null, affiliation: null, website_url: "javascript:alert(1)", openalex_author_id: null }),
      ORIGIN,
    );
    const person = ld.mainEntity as Record<string, unknown>;
    expect(person).not.toHaveProperty("jobTitle");
    expect(person).not.toHaveProperty("affiliation");
    expect(person).not.toHaveProperty("sameAs");
  });
});

describe("paper metadata", () => {
  it("adds short author names to the title", () => {
    expect(authorsShort([{ first_name: "A", last_name: "One" }])).toBe("A One");
    expect(authorsShort([{ first_name: "A", last_name: "One" }, { first_name: "B", last_name: "Two" }])).toBe(
      "One & Two",
    );
    expect(
      authorsShort([
        { first_name: "A", last_name: "One" },
        { first_name: "B", last_name: "Two" },
        { first_name: "C", last_name: "Three" },
      ]),
    ).toBe("One et al.");
    expect(paperTitle(paper())).toBe("Immigration and Wages — Max Steinhardt");
  });

  it("uses the abstract without a scraped 'Abstract' label", () => {
    expect(cleanAbstract("ABSTRACT We study wages.")).toBe("We study wages.");
    expect(cleanAbstract("Abstract: We study wages.")).toBe("We study wages.");
    expect(cleanAbstract("Abstraction matters.")).toBe("Abstraction matters.");
    expect(paperDescription(paper({ abstract: "Abstract — We study wages." }))).toBe("We study wages.");
  });

  it("falls back to status, authors and venue", () => {
    expect(paperDescription(paper({ status: "published", venue: "JLE" }))).toBe(
      "Published by Max Steinhardt (JLE, 2024): “Immigration and Wages”.",
    );
  });

  it("sets canonical and article Open Graph", () => {
    const m = paperMetadata(paper());
    expect(m.alternates?.canonical).toBe("/papers/10");
    expect(m.openGraph).toMatchObject({ type: "article", url: "/papers/10", authors: ["Max Steinhardt"] });
  });
});

describe("paper JSON-LD", () => {
  it("describes a published paper as a ScholarlyArticle with DOI and venue", () => {
    const ld = paperJsonLd(
      paper({ status: "published", venue: "Journal of Labor Economics", doi: "10.1086/123", openalex_id: "W42", abstract: "We study wages." }),
      ORIGIN,
    );
    expect(ld).toEqual({
      "@context": "https://schema.org",
      "@type": "ScholarlyArticle",
      "@id": `${ORIGIN}/papers/10#article`,
      url: `${ORIGIN}/papers/10`,
      mainEntityOfPage: `${ORIGIN}/papers/10`,
      name: "Immigration and Wages",
      headline: "Immigration and Wages",
      author: [{ "@type": "Person", name: "Max Steinhardt", url: `${ORIGIN}/researchers/1` }],
      datePublished: "2024",
      abstract: "We study wages.",
      creativeWorkStatus: "Published",
      isPartOf: { "@type": "Periodical", name: "Journal of Labor Economics" },
      identifier: { "@type": "PropertyValue", propertyID: "DOI", value: "10.1086/123" },
      sameAs: ["https://doi.org/10.1086/123", "https://openalex.org/W42"],
    });
  });

  it("does not claim a journal for working papers or odd years", () => {
    const ld = paperJsonLd(paper({ status: "working_paper", venue: "NBER WP", year: "n.d." }), ORIGIN);
    expect(ld).not.toHaveProperty("isPartOf");
    expect(ld).not.toHaveProperty("datePublished");
    expect(ld).not.toHaveProperty("identifier");
  });

  it("normalises DOI URLs", () => {
    const ld = paperJsonLd(paper({ doi: "https://doi.org/10.1/x" }), ORIGIN);
    expect(ld.identifier).toMatchObject({ value: "10.1/x" });
  });
});

describe("JSON-LD serialisation", () => {
  it("escapes < so data cannot close the script tag", () => {
    const out = serializeJsonLd({ name: "</script><script>alert(1)</script>" });
    expect(out).not.toContain("<");
    expect(JSON.parse(out).name).toBe("</script><script>alert(1)</script>");
  });

  it("describes the site", () => {
    expect(websiteJsonLd(ORIGIN)).toMatchObject({ "@type": "WebSite", name: "Econ Newsfeed", url: `${ORIGIN}/` });
  });
});
