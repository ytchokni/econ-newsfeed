/**
 * Tests for AffiliationLine — catches NULL handling bugs like PR #155.
 *
 * PR #155: ResearcherCard showed blank line when both position and
 * affiliation were null, making it unclear if data was missing or absent.
 */
import { render, screen } from "@testing-library/react";
import AffiliationLine from "../AffiliationLine";

describe("AffiliationLine", () => {
  it("shows placeholder when both position and affiliation are null", () => {
    render(<AffiliationLine position={null} affiliation={null} />);
    expect(screen.getByText("Affiliation unknown")).toBeInTheDocument();
  });

  it("renders placeholder in italic styling", () => {
    render(<AffiliationLine position={null} affiliation={null} />);
    const placeholder = screen.getByText("Affiliation unknown");
    expect(placeholder.classList.contains("italic")).toBe(true);
  });

  it("shows only affiliation when position is null", () => {
    render(<AffiliationLine position={null} affiliation="MIT" />);
    expect(screen.getByText("MIT")).toBeInTheDocument();
    expect(screen.queryByText("Affiliation unknown")).not.toBeInTheDocument();
  });

  it("shows only position when affiliation is null", () => {
    render(<AffiliationLine position="Professor" affiliation={null} />);
    expect(screen.getByText("Professor")).toBeInTheDocument();
    expect(screen.queryByText("Affiliation unknown")).not.toBeInTheDocument();
  });

  it("shows both with comma separator", () => {
    const { container } = render(<AffiliationLine position="Professor" affiliation="MIT" />);
    const p = container.querySelector("p");
    expect(p?.textContent).toBe("Professor, MIT");
  });

  it("does not show comma when only one field present", () => {
    render(<AffiliationLine position="Professor" affiliation={null} />);
    expect(screen.queryByText(",")).not.toBeInTheDocument();
  });

  it("does not render undefined or 'null' as text", () => {
    render(<AffiliationLine position={null} affiliation={null} />);
    const container = screen.getByText("Affiliation unknown").closest("p");
    expect(container?.textContent).not.toContain("undefined");
    expect(container?.textContent).not.toContain("null");
  });

  it("applies custom className", () => {
    const { container } = render(
      <AffiliationLine position="Prof" affiliation="MIT" className="custom-class" />
    );
    expect(container.querySelector(".custom-class")).toBeInTheDocument();
  });
});
