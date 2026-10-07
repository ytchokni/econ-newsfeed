import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import ResearchersContent from "../ResearchersContent";
import type { Researcher } from "@/lib/types";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  usePathname: () => "/researchers",
}));

const researchers: Researcher[] = [
  {
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
  },
  {
    id: 2,
    first_name: "Jane",
    last_name: "Doe",
    position: "Assistant Professor",
    affiliation: "MIT",
    description: null,
    urls: [],
    website_url: null,
    publication_count: 5,
    fields: [],
    jel_codes: [],
  },
];

const emptyFilterOptions = {
  institutions: [],
  positions: [],
  fields: [],
  researchers: [],
};

function renderWithSWR(ui: React.ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), shouldRetryOnError: false }}>
      {ui}
    </SWRConfig>
  );
}

function mockFetchResponses(
  researchersResponse: unknown,
  filterOptionsResponse: unknown = emptyFilterOptions
) {
  (global.fetch as jest.Mock).mockImplementation(async (url: string) => {
    if (url.includes("/api/filter-options")) {
      return { ok: true, json: async () => filterOptionsResponse };
    }
    if (url.includes("/api/researchers")) {
      return { ok: true, json: async () => ({
        total: researchers.length, page: 1, per_page: 100, pages: 1,
        ...(researchersResponse as object),
      }) };
    }
    return { ok: false, status: 404, statusText: "Not Found" };
  });
}

beforeEach(() => {
  jest.resetAllMocks();
  global.fetch = jest.fn();
  window.history.pushState({}, "", "/researchers");
});

describe("ResearchersContent", () => {
  it("shows prompt when no filters are active", async () => {
    mockFetchResponses({ items: researchers });

    renderWithSWR(<ResearchersContent />);

    expect(screen.getByText(/search by name or apply a filter/i)).toBeInTheDocument();
  });

  it("renders researchers when search is active", async () => {
    window.history.pushState({}, "", "/researchers?search=Max");
    mockFetchResponses({ items: researchers });

    renderWithSWR(<ResearchersContent />);

    await waitFor(() => {
      expect(
        screen.getByText("Max Friedrich Steinhardt")
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
  });

  it("shows loading state when filter is active", () => {
    window.history.pushState({}, "", "/researchers?search=test");
    (global.fetch as jest.Mock).mockReturnValue(new Promise(() => {}));

    renderWithSWR(<ResearchersContent />);
    const skeletons = document.querySelectorAll(".animate-pulse");
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("shows error state when filter is active", async () => {
    window.history.pushState({}, "", "/researchers?search=test");
    (global.fetch as jest.Mock).mockImplementation(async (url: string) => {
      if (url.includes("/api/filter-options")) {
        return { ok: true, json: async () => emptyFilterOptions };
      }
      throw new Error("Network error");
    });

    renderWithSWR(<ResearchersContent />);

    await waitFor(() => {
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
    });
  });

  it("renders search input on researchers page", async () => {
    mockFetchResponses({ items: researchers });

    renderWithSWR(<ResearchersContent />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
    });
  });

  it("shows totals and navigates past the first 100 results while keeping filters", async () => {
    window.history.pushState({}, "", "/researchers?search=Researcher&institution=MIT");
    const firstPage = Array.from({ length: 100 }, (_, i) => ({
      ...researchers[1], id: i + 1, first_name: "Researcher", last_name: String(i + 1),
    }));
    const finalResearcher = { ...researchers[1], id: 101, first_name: "Researcher", last_name: "101" };
    (global.fetch as jest.Mock).mockImplementation(async (url: string) => {
      if (url.includes("/api/filter-options")) {
        return { ok: true, json: async () => emptyFilterOptions };
      }
      const params = new URL(url, "http://localhost").searchParams;
      const page = Number(params.get("page"));
      return { ok: true, json: async () => ({
        items: page === 1 ? firstPage : [finalResearcher], total: 101,
        page, per_page: 100, pages: 2,
      }) };
    });

    renderWithSWR(<ResearchersContent />);
    expect(await screen.findByText("Showing 1–100 of 101 researchers")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("Researcher 101")).toBeInTheDocument();
    expect(screen.getByText("Showing 101–101 of 101 researchers")).toBeInTheDocument();
    expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    const page2Url = (global.fetch as jest.Mock).mock.calls.map(([url]) => String(url))
      .find(url => url.includes("page=2"));
    expect(new URL(page2Url!, "http://localhost").searchParams.get("institution")).toBe("MIT");
    expect(new URL(page2Url!, "http://localhost").searchParams.get("search")).toBe("Researcher");

    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Showing 1–100 of 101 researchers")).toBeInTheDocument();
  });

  it("loads a linked page and resets pagination when a filter changes", async () => {
    window.history.pushState({}, "", "/researchers?search=Jane&page=2");
    (global.fetch as jest.Mock).mockImplementation(async (url: string) => {
      if (url.includes("/api/filter-options")) {
        return { ok: true, json: async () => emptyFilterOptions };
      }
      const page = Number(new URL(url, "http://localhost").searchParams.get("page"));
      return { ok: true, json: async () => ({ items: [researchers[1]], total: 101, page, per_page: 100, pages: 2 }) };
    });
    renderWithSWR(<ResearchersContent />);
    expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Top-20 Depts" }));
    expect(await screen.findByText("Page 1 of 2")).toBeInTheDocument();
    const filteredUrl = (global.fetch as jest.Mock).mock.calls.map(([url]) => String(url))
      .find(url => url.includes("preset=top20"));
    expect(new URL(filteredUrl!, "http://localhost").searchParams.get("page")).toBe("1");
    expect(new URL(filteredUrl!, "http://localhost").searchParams.get("search")).toBe("Jane");
  });
});
