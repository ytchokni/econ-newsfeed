import { render, screen } from "@testing-library/react";
import PaperDetailContent from "../[id]/PaperDetailContent";
import type { PublicationDetail } from "@/lib/types";

// Mock next/navigation
const mockBack = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ back: mockBack }),
}));

// Mock the usePublication hook
const basePublication: PublicationDetail = {
  id: 1,
  title: "Trade and Wages: Evidence from Germany",
  authors: [
    { id: 10, first_name: "Max", last_name: "Steinhardt", affiliation: "FU Berlin" },
    { id: 11, first_name: "Jane", last_name: "Doe", affiliation: null },
  ],
  year: "2024",
  venue: "Journal of Labor Economics",
  source_url: "https://example.com/pub",
  discovered_at: "2026-03-15T14:30:00Z",
  status: "published",
  abstract: "This paper studies the effects of immigration on wages.",
  draft_url: "https://ssrn.com/abstract=1",
  draft_url_status: "valid",
  draft_available: true,
  doi: "10.1257/aer.20181234",
  coauthors: [
    { display_name: "Max Steinhardt", openalex_author_id: "A111" },
    { display_name: "Jane Doe", openalex_author_id: "A222" },
  ],
  links: [
    { url: "https://ssrn.com/abstract=1", link_type: "ssrn" },
  ],
  feed_events: [
    { id: 5, event_type: "status_change", old_status: "working_paper", new_status: "published", created_at: "2026-03-20T12:00:00Z" },
    { id: 1, event_type: "new_paper", old_status: null, new_status: null, created_at: "2026-03-15T14:30:00Z" },
  ],
  history: [
    { status: "published", venue: "JLE", abstract: null, draft_url: "https://ssrn.com/abstract=1", draft_url_status: "valid", year: "2024", scraped_at: "2026-03-20T12:00:00Z", source_url: "https://example.com/pub" },
    { status: "working_paper", venue: null, abstract: null, draft_url: "https://ssrn.com/abstract=1", draft_url_status: "valid", year: "2024", scraped_at: "2026-03-15T14:30:00Z", source_url: "https://example.com/pub" },
  ],
  is_seed: false,
  title_hash: "abc123def456",
  openalex_id: "W12345",
};

let mockPublication = basePublication;

beforeEach(() => {
  mockPublication = { ...basePublication };
});

jest.mock("@/lib/api", () => ({
  usePublication: () => ({
    data: mockPublication,
    error: undefined,
    isLoading: false,
  }),
}));

describe("PaperDetailContent", () => {
  it("renders the paper title", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText("Trade and Wages: Evidence from Germany")).toBeInTheDocument();
  });

  it("renders the abstract", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText(/effects of immigration on wages/)).toBeInTheDocument();
  });

  it("renders author links", () => {
    render(<PaperDetailContent id={1} />);
    const links = screen.getAllByRole("link");
    const authorLinks = links.filter(l => l.getAttribute("href")?.startsWith("/researchers/"));
    expect(authorLinks.length).toBeGreaterThanOrEqual(2);
  });

  it("renders status pill", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText("Published")).toBeInTheDocument();
  });

  it("renders DOI link", () => {
    render(<PaperDetailContent id={1} />);
    const doiLink = screen.getByText("DOI").closest("a");
    expect(doiLink).toHaveAttribute("href", "https://doi.org/10.1257/aer.20181234");
  });

  it("provides a public source link alongside existing paper links", () => {
    render(<PaperDetailContent id={1} />);
    const sourceLink = screen.getByRole("link", { name: "Source page" });
    expect(sourceLink).toHaveAttribute("href", "https://example.com/pub");
    expect(sourceLink).toHaveAttribute("target", "_blank");
    expect(sourceLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "DOI" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Draft" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SSRN" })).toBeInTheDocument();
  });

  it("provides the source page when no direct paper links are available", () => {
    mockPublication = { ...basePublication, doi: null, draft_url: null, draft_available: false, links: [] };
    render(<PaperDetailContent id={1} />);
    expect(screen.getByRole("link", { name: "Source page" })).toHaveAttribute("href", "https://example.com/pub");
  });

  it.each([null, "javascript:alert(1)", "data:text/html,test", "ftp://example.com/pub", "/relative", "https://"])(
    "does not link to a missing, unsafe or malformed source (%p)",
    (source_url) => {
      mockPublication = { ...basePublication, source_url };
      render(<PaperDetailContent id={1} />);
      expect(screen.queryByRole("link", { name: "Source page" })).not.toBeInTheDocument();
    }
  );

  it("renders history timeline with feed events", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText(/Discovered/)).toBeInTheDocument();
    expect(screen.getByText(/Status changed/)).toBeInTheDocument();
  });

  it("links a historical update to its own source", () => {
    mockPublication = {
      ...basePublication,
      feed_events: [{ ...basePublication.feed_events[0], event_source_url: "https://example.com/older-source" }],
    };
    render(<PaperDetailContent id={1} />);
    expect(screen.getByRole("link", { name: "Source for this update" })).toHaveAttribute("href", "https://example.com/older-source");
    expect(screen.getByRole("link", { name: "Source page" })).toHaveAttribute("href", "https://example.com/pub");
  });

  it("does not link an update to a non-web source", () => {
    mockPublication = {
      ...basePublication,
      feed_events: [{ ...basePublication.feed_events[0], event_source_url: "javascript:alert(1)" }],
    };
    render(<PaperDetailContent id={1} />);
    expect(screen.queryByRole("link", { name: "Source for this update" })).not.toBeInTheDocument();
  });

  it("renders back link", () => {
    render(<PaperDetailContent id={1} />);
    expect(screen.getByText(/Back/)).toBeInTheDocument();
  });
});
