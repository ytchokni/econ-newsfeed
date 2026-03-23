import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PublicationCard from "../PublicationCard";
import type { Publication } from "@/lib/types";

// Mock next/navigation
const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

const publication: Publication = {
  id: 1,
  title: "Immigration and Wages: Evidence from Germany",
  authors: [
    { id: 1, first_name: "Max", last_name: "Steinhardt" },
    { id: 2, first_name: "Jane", last_name: "Doe" },
  ],
  year: "2024",
  venue: "Journal of Labor Economics",
  source_url: "https://example.com/paper",
  discovered_at: "2026-03-15T14:30:00Z",
  status: null,
  abstract: null,
  draft_url: null,
  draft_url_status: "unchecked",
  draft_available: false,
  doi: null,
  coauthors: [],
  links: [],
};

describe("PublicationCard", () => {
  it("renders the publication title", () => {
    render(<PublicationCard publication={publication} />);
    expect(
      screen.getByText("Immigration and Wages: Evidence from Germany")
    ).toBeInTheDocument();
  });

  it("renders author names", () => {
    render(<PublicationCard publication={publication} />);
    expect(screen.getByText(/M\. Steinhardt/)).toBeInTheDocument();
    expect(screen.getByText(/J\. Doe/)).toBeInTheDocument();
  });

  it("renders venue and year", () => {
    render(<PublicationCard publication={publication} />);
    expect(screen.getByText(/Journal of Labor Economics/)).toBeInTheDocument();
    expect(screen.getByText(/2024/)).toBeInTheDocument();
  });

  it("links author names to researcher pages", () => {
    render(<PublicationCard publication={publication} />);
    const links = screen.getAllByRole("link");
    const authorLinks = links.filter((l) =>
      l.getAttribute("href")?.startsWith("/researchers/")
    );
    expect(authorLinks).toHaveLength(2);
    expect(authorLinks[0]).toHaveAttribute("href", "/researchers/1");
    expect(authorLinks[1]).toHaveAttribute("href", "/researchers/2");
  });
});

describe("PublicationCard navigation", () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  it("navigates to paper detail on card click", async () => {
    const user = userEvent.setup();
    render(<PublicationCard publication={publication} />);
    const card = screen.getByText("Immigration and Wages: Evidence from Germany").closest("[data-testid='publication-card']")!;
    await user.click(card);
    expect(mockPush).toHaveBeenCalledWith("/papers/1");
  });

  it("does not navigate when clicking author link", async () => {
    const user = userEvent.setup();
    render(<PublicationCard publication={publication} />);
    const authorLink = screen.getByText(/M\. Steinhardt/);
    await user.click(authorLink);
    expect(mockPush).not.toHaveBeenCalled();
  });
});

describe("PublicationCard OpenAlex fields", () => {
  const pubWithDoi: Publication = {
    ...publication,
    doi: "10.1257/aer.20181234",
    coauthors: [
      { display_name: "Max Steinhardt", openalex_author_id: "A111" },
      { display_name: "Jane Doe", openalex_author_id: "A222" },
    ],
  };

  it("renders DOI link when doi is present", () => {
    render(<PublicationCard publication={pubWithDoi} />);
    const doiLink = screen.getByText("DOI").closest("a");
    expect(doiLink).toHaveAttribute("href", "https://doi.org/10.1257/aer.20181234");
    expect(doiLink).toHaveAttribute("target", "_blank");
  });

  it("does not render DOI link when doi is null", () => {
    render(<PublicationCard publication={publication} />);
    expect(screen.queryByText("DOI")).not.toBeInTheDocument();
  });

  it("renders OpenAlex co-authors", () => {
    render(<PublicationCard publication={pubWithDoi} />);
    expect(screen.getByText(/All authors:/)).toBeInTheDocument();
    expect(screen.getByText(/Max Steinhardt, Jane Doe/)).toBeInTheDocument();
  });

  it("does not render co-authors section when empty", () => {
    render(<PublicationCard publication={publication} />);
    expect(screen.queryByText(/All authors:/)).not.toBeInTheDocument();
  });
});
