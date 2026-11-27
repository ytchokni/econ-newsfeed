import { render, screen, within } from "@testing-library/react";
import ContactPage from "../contact/page";
import PrivacyPage from "../privacy/page";
import SiteFooter from "@/components/SiteFooter";
import { genericRemovalMailto, researcherRemovalMailto } from "@/lib/removal";
import { getContactEmail } from "@/lib/site-info";

const ORIGINAL_ENV = process.env;

beforeEach(() => {
  process.env = { ...ORIGINAL_ENV };
  delete process.env.CONTACT_EMAIL;
  delete process.env.SITE_URL;
});

afterAll(() => {
  process.env = ORIGINAL_ENV;
});

describe("site info", () => {
  it("reads a valid CONTACT_EMAIL and ignores junk", () => {
    expect(getContactEmail()).toBeNull();
    process.env.CONTACT_EMAIL = " owner@example.test ";
    expect(getContactEmail()).toBe("owner@example.test");
    process.env.CONTACT_EMAIL = "not an email";
    expect(getContactEmail()).toBeNull();
  });
});

describe("removal mailto links", () => {
  it("are null without a contact address", () => {
    expect(genericRemovalMailto(null)).toBeNull();
    expect(researcherRemovalMailto(null, "https://s", { id: 1, firstName: "A", lastName: "B" })).toBeNull();
  });

  it("encode spaces as %20 and omit the website line when unknown", () => {
    const href = researcherRemovalMailto("o@example.test", "https://s.example", {
      id: 7,
      firstName: "Ana",
      lastName: "Ruiz & Co",
    })!;
    expect(href).not.toContain("+");
    const params = new URLSearchParams(href.split("?")[1]);
    expect(params.get("subject")).toBe("Removal request: Ana Ruiz & Co (researcher #7)");
    expect(params.get("body")).not.toContain("Website:");
  });
});

describe("contact page (Impressum)", () => {
  it("shows owner placeholders and the configured address", () => {
    process.env.CONTACT_EMAIL = "owner@example.test";
    render(<ContactPage />);
    expect(screen.getByRole("heading", { name: /Impressum \(Angaben gemäß § 5 DDG\)/ })).toBeInTheDocument();
    expect(screen.getAllByText(/TO BE COMPLETED BY SITE OWNER: full name/).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "owner@example.test" })).toHaveAttribute(
      "href",
      "mailto:owner@example.test",
    );
    expect(screen.getByRole("link", { name: "Request removal by email" }).getAttribute("href")).toMatch(
      /^mailto:owner@example\.test\?subject=Removal%20request/,
    );
    expect(screen.getAllByText(/EconNewsfeedBot/).length).toBeGreaterThan(0);
  });

  it("marks the address as missing when CONTACT_EMAIL is not set", () => {
    render(<ContactPage />);
    expect(screen.getByText(/set CONTACT_EMAIL/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Request removal by email" })).toBeNull();
  });
});

describe("privacy page", () => {
  it("covers processing, processors, legal bases, retention and rights", () => {
    process.env.CONTACT_EMAIL = "owner@example.test";
    render(<PrivacyPage />);
    const text = document.body.textContent || "";
    for (const expected of [
      "Art. 6(1)(f)",
      "Art. 6(1)(b)",
      "Art. 21",
      "Art. 77",
      "Vercel",
      "Hetzner",
      "Resend",
      "Google AI Studio",
      "OpenAlex",
      "Crossref",
      "How long we keep data",
      "TO BE COMPLETED BY SITE OWNER",
    ]) {
      expect(text).toContain(expected);
    }
    const researchers = document.getElementById("researchers")!;
    expect(within(researchers).getByRole("link", { name: "Request removal by email" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^mailto:owner@example\.test/),
    );
  });
});

describe("site footer", () => {
  it("links the legal pages and the removal request", () => {
    process.env.CONTACT_EMAIL = "owner@example.test";
    render(<SiteFooter />);
    const nav = screen.getByRole("navigation", { name: "Legal" });
    expect(within(nav).getByRole("link", { name: /Contact & Impressum/ })).toHaveAttribute("href", "/contact");
    expect(within(nav).getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "/privacy");
    expect(within(nav).getByRole("link", { name: /request removal/ }).getAttribute("href")).toMatch(/^mailto:/);
  });

  it("falls back to the privacy section without an address", () => {
    render(<SiteFooter />);
    expect(screen.getByRole("link", { name: /request removal/ })).toHaveAttribute("href", "/privacy#researchers");
  });
});
