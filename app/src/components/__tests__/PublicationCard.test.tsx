import { render, screen } from "@testing-library/react";
import PublicationCard from "../PublicationCard";
import type { Publication } from "@/lib/types";

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
