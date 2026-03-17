import { render, screen } from "@testing-library/react";
import Header from "../Header";

describe("Header", () => {
  it("renders the site title", () => {
    render(<Header />);
    expect(screen.getByText("Econ Newsfeed")).toBeInTheDocument();
  });

  it("renders navigation links", () => {
    render(<Header />);
    expect(screen.getByRole("link", { name: /^Feed$/i })).toHaveAttribute(
      "href",
      "/"
    );
    expect(
      screen.getByRole("link", { name: /^Researchers$/i })
    ).toHaveAttribute("href", "/researchers");
  });
});
