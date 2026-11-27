import { render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
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

  it("leaves the date out of server HTML so hydration cannot mismatch", () => {
    const html = renderToString(<Header />);
    expect(html).not.toMatch(/Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday/);
  });

  it("shows today's date on the client, hidden on narrow screens", () => {
    render(<Header />);
    const today = new Date().toLocaleDateString("en-US", { weekday: "long" });
    const date = screen.getByText(new RegExp(today));
    expect(date).toHaveClass("hidden", "sm:inline");
  });
});
