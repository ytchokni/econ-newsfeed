/**
 * Edge-case tests for PublicationCard — null/missing fields must not crash.
 *
 * Catches bugs like PR #146 (65% NULL years), #155 (NULL affiliations),
 * and ensures status_change events with null statuses render safely.
 */
import { render, screen } from "@testing-library/react";
import PublicationCard from "../PublicationCard";
import type { Publication } from "@/lib/types";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

const basePublication: Publication = {
  id: 1,
  title: "Test Paper Title",
  authors: [{ id: 1, first_name: "Jane", last_name: "Doe", affiliation: null }],
  year: null,
  venue: null,
  source_url: null,
  discovered_at: "2026-01-01T00:00:00Z",
  status: null,
  abstract: null,
  draft_url: null,
  draft_url_status: "unchecked",
  draft_available: false,
  doi: null,
  coauthors: [],
  links: [],
};

describe("PublicationCard with null fields", () => {
  it("renders without crashing when year is null", () => {
    render(<PublicationCard publication={{ ...basePublication, year: null }} />);
    expect(screen.getByText("Test Paper Title")).toBeInTheDocument();
  });

  it("renders without crashing when venue is null", () => {
    render(<PublicationCard publication={{ ...basePublication, venue: null }} />);
    expect(screen.getByText("Test Paper Title")).toBeInTheDocument();
  });

  it("renders without crashing when both year and venue are null", () => {
    render(<PublicationCard publication={basePublication} />);
    expect(screen.getByText("Test Paper Title")).toBeInTheDocument();
  });

  it("does not render venue/year separator when both are null", () => {
    const { container } = render(<PublicationCard publication={basePublication} />);
    expect(container.textContent).not.toContain("·");
  });

  it("renders venue without year correctly", () => {
    render(<PublicationCard publication={{ ...basePublication, venue: "AER" }} />);
    expect(screen.getByText("AER")).toBeInTheDocument();
  });

  it("renders year without venue correctly", () => {
    render(<PublicationCard publication={{ ...basePublication, year: "2024" }} />);
    expect(screen.getByText(/2024/)).toBeInTheDocument();
  });

  it("renders both venue and year comma-separated", () => {
    render(<PublicationCard publication={{ ...basePublication, venue: "AER", year: "2024" }} />);
    expect(screen.getByText("AER, 2024")).toBeInTheDocument();
  });

  it("renders without crashing when status is null", () => {
    render(<PublicationCard publication={{ ...basePublication, status: null }} />);
    expect(screen.getByText("Test Paper Title")).toBeInTheDocument();
  });

  it("does not render status pill when status is null", () => {
    render(<PublicationCard publication={basePublication} />);
    expect(screen.queryByText("Working Paper")).not.toBeInTheDocument();
    expect(screen.queryByText("Published")).not.toBeInTheDocument();
  });

  it("renders status pill when status is provided", () => {
    render(<PublicationCard publication={{ ...basePublication, status: "working_paper" }} />);
    expect(screen.getByText("Working Paper")).toBeInTheDocument();
  });

  it("renders without crashing when authors array is empty", () => {
    render(<PublicationCard publication={{ ...basePublication, authors: [] }} />);
    expect(screen.getByText("Test Paper Title")).toBeInTheDocument();
  });

  it("does not render DOI link when doi is null", () => {
    render(<PublicationCard publication={basePublication} />);
    expect(screen.queryByText("(DOI)")).not.toBeInTheDocument();
  });

  it("does not render draft link when draft_available is false", () => {
    render(<PublicationCard publication={basePublication} />);
    expect(screen.queryByText("(Link)")).not.toBeInTheDocument();
  });

  it("does not render abstract toggle when abstract is null", () => {
    render(<PublicationCard publication={basePublication} />);
    expect(screen.queryByText("Abstract")).not.toBeInTheDocument();
  });

  it("does not render coauthors section when coauthors is empty", () => {
    render(<PublicationCard publication={basePublication} />);
    expect(screen.queryByText(/All authors:/)).not.toBeInTheDocument();
  });

  it("does not render link pills when links is empty", () => {
    render(<PublicationCard publication={basePublication} />);
    expect(screen.queryByText("PDF")).not.toBeInTheDocument();
    expect(screen.queryByText("SSRN")).not.toBeInTheDocument();
  });
});

describe("PublicationCard status change events", () => {
  it.each([null, "", "   "])("omits 'at' for a missing venue (%p), even when the year is known", (venue) => {
    render(
      <PublicationCard publication={{
        ...basePublication,
        venue,
        year: "2026",
        event_type: "status_change",
        old_status: "working_paper",
        new_status: "revise_and_resubmit",
      }} />
    );
    expect(screen.queryByText("at")).not.toBeInTheDocument();
    expect(screen.getByText("Revise & Resubmit")).toBeInTheDocument();
    expect(screen.getByText("(2026)")).toBeInTheDocument();
  });

  it("uses the venue recorded for the event and shows the year separately", () => {
    render(
      <PublicationCard publication={{
        ...basePublication,
        venue: "Current Journal",
        event_venue: "Event Journal",
        year: "2026",
        event_type: "status_change",
        old_status: "working_paper",
        new_status: "accepted",
      }} />
    );
    expect(screen.getByText("at")).toBeInTheDocument();
    expect(screen.getByText("Event Journal")).toBeInTheDocument();
    expect(screen.getByText("(2026)")).toBeInTheDocument();
    expect(screen.queryByText(/Current Journal/)).not.toBeInTheDocument();
  });

  it("does not substitute the current venue when the event explicitly had none", () => {
    render(
      <PublicationCard publication={{
        ...basePublication,
        venue: "Current Journal",
        event_venue: null,
        event_type: "status_change",
        old_status: "working_paper",
        new_status: "revise_and_resubmit",
      }} />
    );
    expect(screen.queryByText("at")).not.toBeInTheDocument();
    expect(screen.queryByText(/Current Journal/)).not.toBeInTheDocument();
  });

  it("falls back to the paper venue for older API responses", () => {
    render(
      <PublicationCard publication={{
        ...basePublication,
        venue: "Journal of Labor Economics",
        event_type: "status_change",
        old_status: "working_paper",
        new_status: "accepted",
      }} />
    );
    expect(screen.getByText("at")).toBeInTheDocument();
    expect(screen.getByText("Journal of Labor Economics")).toBeInTheDocument();
  });

  it("renders status change transition chips", () => {
    render(
      <PublicationCard
        publication={{
          ...basePublication,
          event_type: "status_change",
          old_status: "working_paper",
          new_status: "accepted",
        }}
      />
    );
    expect(screen.getByText("Working Paper")).toBeInTheDocument();
    expect(screen.getByText("Accepted")).toBeInTheDocument();
  });

  it("does not render status change transition for new_paper events", () => {
    render(
      <PublicationCard
        publication={{
          ...basePublication,
          event_type: "new_paper",
          status: "working_paper",
        }}
      />
    );
    expect(screen.queryByText("→")).not.toBeInTheDocument();
  });

  it("hides status pill during status_change event to avoid duplication", () => {
    render(
      <PublicationCard
        publication={{
          ...basePublication,
          event_type: "status_change",
          old_status: "working_paper",
          new_status: "accepted",
          status: "accepted",
        }}
      />
    );
    const pills = screen.getAllByText("Accepted");
    expect(pills).toHaveLength(1);
  });
});
