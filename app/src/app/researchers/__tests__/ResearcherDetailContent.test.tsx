import { render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import ResearcherDetailContent from "../../researchers/[id]/ResearcherDetailContent";
import type { ResearcherDetail } from "@/lib/types";

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
  publications: [
    {
      id: 1,
      title: "Immigration and Wages",
      authors: [{ id: 1, first_name: "Max Friedrich", last_name: "Steinhardt" }],
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
    },
    {
      id: 2,
      title: "Trade Shocks",
      authors: [
        { id: 1, first_name: "Max Friedrich", last_name: "Steinhardt" },
        { id: 2, first_name: "Jane", last_name: "Doe" },
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
    expect(screen.getByText(/Freie Universität Berlin/)).toBeInTheDocument();
    expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    expect(screen.getByText("Trade Shocks")).toBeInTheDocument();
  });

  it("shows loading state", () => {
    (global.fetch as jest.Mock).mockReturnValueOnce(new Promise(() => {}));

    renderWithSWR(<ResearcherDetailContent id={1} />);
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
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
