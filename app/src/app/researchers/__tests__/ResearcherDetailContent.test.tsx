import { render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import ResearcherDetailContent from "../../researchers/[id]/ResearcherDetailContent";
import type { ResearcherDetail } from "@/lib/types";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
}));

const researcher: ResearcherDetail = {
  id: 1,
  first_name: "Max Friedrich",
  last_name: "Steinhardt",
  position: "Professor",
  affiliation: "Freie Universität Berlin",
  description: null,
  urls: [],
  website_url: null,
  publication_count: 2,
  fields: [],
  jel_codes: [],
  publications: [
    {
      id: 1,
      title: "Immigration and Wages",
      authors: [{ id: 1, first_name: "Max Friedrich", last_name: "Steinhardt", affiliation: "Freie Universität Berlin" }],
      year: "2024",
      venue: "JLE",
      source_url: null,
      discovered_at: "2026-03-15T14:30:00Z",
      status: null,
      abstract: null,
      draft_url: null,
      draft_url_status: "unchecked" as const,
      draft_available: false,
      doi: null,
      coauthors: [],
      links: [],
    },
    {
      id: 2,
      title: "Trade Shocks",
      authors: [
        { id: 1, first_name: "Max Friedrich", last_name: "Steinhardt", affiliation: "Freie Universität Berlin" },
        { id: 2, first_name: "Jane", last_name: "Doe", affiliation: null },
      ],
      year: "2025",
      venue: "WP",
      source_url: null,
      discovered_at: "2026-03-14T10:00:00Z",
      status: null,
      abstract: null,
      draft_url: null,
      draft_url_status: "unchecked" as const,
      draft_available: false,
      doi: null,
      coauthors: [],
      links: [],
    },
  ],
};

function renderWithSWR(ui: React.ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), shouldRetryOnError: false }}>
      {ui}
    </SWRConfig>
  );
}

beforeEach(() => {
  jest.resetAllMocks();
  global.fetch = jest.fn();
});

describe("ResearcherDetailContent", () => {
  it("renders researcher profile and publications", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => researcher,
    });

    renderWithSWR(<ResearcherDetailContent id={1} />);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: "Max Friedrich Steinhardt" })
      ).toBeInTheDocument();
    });

    expect(screen.getByText(/Professor/)).toBeInTheDocument();
    expect(screen.getAllByText(/Freie Universität Berlin/).length).toBeGreaterThan(0);
    expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    expect(screen.getByText("Trade Shocks")).toBeInTheDocument();
  });

  it("offers a prefilled removal request to the researcher", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ...researcher, website_url: "https://steinhardt.example/" }),
    });

    renderWithSWR(
      <ResearcherDetailContent id={1} contactEmail="owner@example.test" siteUrl="https://site.example" />,
    );

    const link = await screen.findByRole("link", { name: "Request removal" });
    const href = link.getAttribute("href")!;
    expect(href.startsWith("mailto:owner@example.test?subject=")).toBe(true);
    const params = new URLSearchParams(href.split("?")[1]);
    expect(params.get("subject")).toBe("Removal request: Max Friedrich Steinhardt (researcher #1)");
    expect(params.get("body")).toContain("Researcher ID: 1");
    expect(params.get("body")).toContain("Profile: https://site.example/researchers/1");
    expect(params.get("body")).toContain("Website: https://steinhardt.example/");
  });

  it("links removal to the contact page when no address is configured", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => researcher });
    renderWithSWR(<ResearcherDetailContent id={1} />);
    expect(await screen.findByRole("link", { name: "Request removal" })).toHaveAttribute(
      "href",
      "/contact#researchers",
    );
  });

  it("shows loading state", () => {
    (global.fetch as jest.Mock).mockReturnValueOnce(new Promise(() => {}));

    renderWithSWR(<ResearcherDetailContent id={1} />);
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });

  it("shows placeholder when affiliation is null", async () => {
    const noAffiliation = { ...researcher, position: null, affiliation: null };
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => noAffiliation,
    });

    renderWithSWR(<ResearcherDetailContent id={1} />);

    await waitFor(() => {
      expect(screen.getByText("Affiliation unknown")).toBeInTheDocument();
    });
  });

  it("shows error state", async () => {
    (global.fetch as jest.Mock).mockRejectedValueOnce(
      new Error("Not found")
    );

    renderWithSWR(<ResearcherDetailContent id={999} />);

    await waitFor(() => {
      expect(screen.getByText(/not found/i)).toBeInTheDocument();
    });
  });
});
