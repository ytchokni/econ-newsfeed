import { render, screen } from "@testing-library/react";
import ResearcherCard from "../ResearcherCard";
import type { Researcher } from "@/lib/types";

const researcher: Researcher = {
  id: 1,
  first_name: "Max Friedrich",
  last_name: "Steinhardt",
  position: "Professor",
  affiliation: "Freie Universität Berlin",
  description: null,
  urls: [],
  website_url: null,
  publication_count: 23,
  fields: [],
  jel_codes: [],
};

describe("ResearcherCard", () => {
  it("renders the researcher name", () => {
    render(<ResearcherCard researcher={researcher} />);
    expect(
      screen.getByText("Max Friedrich Steinhardt")
    ).toBeInTheDocument();
  });

  it("renders affiliation and position", () => {
    render(<ResearcherCard researcher={researcher} />);
    expect(screen.getByText(/Professor/)).toBeInTheDocument();
    expect(
      screen.getByText(/Freie Universität Berlin/)
    ).toBeInTheDocument();
  });

  it("renders publication count", () => {
    render(<ResearcherCard researcher={researcher} />);
    expect(screen.getByText(/23 publications/)).toBeInTheDocument();
  });

  it("links to the researcher detail page", () => {
    render(<ResearcherCard researcher={researcher} />);
    const links = screen.getAllByRole("link");
    const detailLinks = links.filter((l) =>
      l.getAttribute("href") === "/researchers/1"
    );
    expect(detailLinks.length).toBeGreaterThanOrEqual(1);
  });
});
