import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SWRConfig } from "swr";
import NewsfeedContent from "../NewsfeedContent";
import type { PaginatedResponse, Publication } from "@/lib/types";

const mockReplace = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: mockReplace }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  usePathname: () => "/",
}));

const page1: PaginatedResponse<Publication> = {
  items: [
    {
      id: 1,
      title: "Immigration and Wages",
      authors: [{ id: 1, first_name: "Max", last_name: "Steinhardt", affiliation: null }],
      year: "2024",
      venue: "JLE",
      source_url: null,
      discovered_at: "2026-03-15T14:30:00Z",
      status: null,
      abstract: null,
      draft_url: null,
      draft_url_status: "unchecked" as const,
      draft_available: false,
      doi: null,
      coauthors: [],
      links: [],
    },
    {
      id: 2,
      title: "Trade Shocks",
      authors: [{ id: 2, first_name: "Jane", last_name: "Doe", affiliation: null }],
      year: "2025",
      venue: "WP",
      source_url: null,
      discovered_at: "2026-03-14T10:00:00Z",
      status: null,
      abstract: null,
      draft_url: null,
      draft_url_status: "unchecked" as const,
      draft_available: false,
      doi: null,
      coauthors: [],
      links: [],
    },
  ],
  total: 3,
  page: 1,
  per_page: 20,
  pages: 2,
};

const page2: PaginatedResponse<Publication> = {
  items: [
    {
      id: 3,
      title: "Fiscal Policy in Europe",
      authors: [{ id: 1, first_name: "Max", last_name: "Steinhardt", affiliation: null }],
      year: "2023",
      venue: "QJE",
      source_url: null,
      discovered_at: "2026-03-13T08:00:00Z",
      status: null,
      abstract: null,
      draft_url: null,
      draft_url_status: "unchecked" as const,
      draft_available: false,
      doi: null,
      coauthors: [],
      links: [],
    },
  ],
  total: 3,
  page: 2,
  per_page: 20,
  pages: 2,
};

function renderWithSWR(ui: React.ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), shouldRetryOnError: false }}>
      {ui}
    </SWRConfig>
  );
}

const emptyFilterOptions = {
  institutions: [],
  positions: [],
  fields: [],
};

const emptyJelCodes = { items: [] };

function mockFetch(publicationsResponse: unknown) {
  (global.fetch as jest.Mock).mockImplementation((url: string) => {
    if (url.includes("/api/filter-options")) {
      return Promise.resolve({ ok: true, json: async () => emptyFilterOptions });
    }
    if (url.includes("/api/jel-codes")) {
      return Promise.resolve({ ok: true, json: async () => emptyJelCodes });
    }
    if (typeof publicationsResponse === "function") {
      return publicationsResponse(url);
    }
    return Promise.resolve({ ok: true, json: async () => publicationsResponse });
  });
}

beforeEach(() => {
  jest.resetAllMocks();
  global.fetch = jest.fn();
  window.history.replaceState({}, "", "/");
});

afterEach(() => {
  jest.useRealTimers();
});

describe("NewsfeedContent", () => {
  it.each([
    ["This month", "2026-10-01"],
    ["This quarter", "2026-10-01"],
    ["Last 7 days", "2026-10-05"],
  ])("uses the local calendar date for %s", async (label, expectedSince) => {
    // Local midnight is the previous UTC day in Europe/Berlin.
    jest.useFakeTimers().setSystemTime(new Date(2026, 9, 12, 0, 30));
    mockFetch(page1);
    renderWithSWR(<NewsfeedContent />);
    await screen.findByText("Immigration and Wages");
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    await user.click(screen.getByRole("button", { name: "Filters (1)" }));
    await user.click(screen.getByRole("button", { name: label }));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining(`since=${expectedSince}`));
    });
  });

  it("renders publications grouped by discovery date", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    });

    expect(screen.getByText("Trade Shocks")).toBeInTheDocument();
  });

  it("shows loading skeletons initially", () => {
    (global.fetch as jest.Mock).mockReturnValue(new Promise(() => {}));

    const { container } = renderWithSWR(<NewsfeedContent />);
    expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("shows error state on fetch failure", async () => {
    (global.fetch as jest.Mock).mockImplementation((url: string) => {
      if (url.includes("/api/filter-options")) {
        return Promise.resolve({ ok: true, json: async () => emptyFilterOptions });
      }
      if (url.includes("/api/jel-codes")) {
        return Promise.resolve({ ok: true, json: async () => emptyJelCodes });
      }
      return Promise.reject(new Error("Network error"));
    });

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
    });
  });

  it("shows Next button when more pages exist", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: /next/i })).toBeInTheDocument();
  });

  it("renders search input on newsfeed page", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
    });
  });

  it("renders All as the last tab", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^all$/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /work in progress/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /working papers/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /publications/i })).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /^(All|Work in Progress|Working Papers|Publications)$/i })
        .map((button) => button.textContent)
    ).toEqual(["Work in Progress", "Working Papers", "Publications", "All"]);
  });

  it("defaults to Publications filtered to Top-5 and top field journals", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    });

    const pubCalls = (global.fetch as jest.Mock).mock.calls
      .map(([url]: [string]) => url)
      .filter((url: string) => url.includes("/api/publications"));
    expect(pubCalls.length).toBeGreaterThan(0);
    const params = new URL(pubCalls[0], "http://localhost").searchParams;
    expect(params.get("event_type")).toBe("status_change");
    expect(params.get("status")).toBe("revise_and_resubmit,accepted,published");
    expect(params.get("preset")).toBe("top5_and_field");
    expect(screen.getByRole("button", { name: "Remove Top-5 & Top Field Journals" })).toBeInTheDocument();
  });

  it("switches from All to Publications", async () => {
    window.history.replaceState({}, "", "?tab=all");
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Publications" }));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("event_type=status_change")
      );
    });
  });

  it("keeps an explicit All view unfiltered when opening a shared link", async () => {
    window.history.replaceState({}, "", "?tab=all");
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/publications?page=1&per_page=20");
    });
  });

  it("removes only the selected filter when its bubble cross is clicked", async () => {
    window.history.replaceState({}, "", "?preset=top5_journals&search=trade");
    mockFetch(page1);
    const user = userEvent.setup();

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("preset=top5_journals"));
    });
    expect(screen.getByRole("button", { name: "Remove Top-5 Journals" })).toBeInTheDocument();
    expect(screen.getByText("2 filters applied")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove Top-5 Journals" }));

    expect(screen.queryByRole("button", { name: "Remove Top-5 Journals" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Search: trade" })).toBeInTheDocument();
    expect(screen.getByText("1 filter applied")).toBeInTheDocument();
    expect(mockReplace).toHaveBeenLastCalledWith("/?tab=publications&search=trade", { scroll: false });
  });

  it("keeps a filter removed via its bubble cross cleared after reopening the URL", async () => {
    mockFetch(page1);
    const user = userEvent.setup();
    const view = renderWithSWR(<NewsfeedContent />);

    await user.click(screen.getByRole("button", { name: "Remove Top-5 & Top Field Journals" }));

    expect(screen.queryByRole("region", { name: "Applied filters" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument();
    expect(mockReplace).toHaveBeenLastCalledWith("/?tab=publications", { scroll: false });
    view.unmount();
    window.history.replaceState({}, "", mockReplace.mock.calls.at(-1)[0]);
    (global.fetch as jest.Mock).mockClear();
    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("event_type=status_change"));
    });
    const pubCalls = (global.fetch as jest.Mock).mock.calls
      .map(([url]: [string]) => url)
      .filter((url: string) => url.includes("/api/publications"));
    expect(pubCalls.every((url: string) => !url.includes("preset="))).toBe(true);
  });

  it("writes the All tab explicitly so it survives reopening the URL", async () => {
    mockFetch(page1);
    const user = userEvent.setup();
    renderWithSWR(<NewsfeedContent />);

    await user.click(screen.getByRole("button", { name: "All" }));

    expect(mockReplace).toHaveBeenLastCalledWith(
      "/?tab=all&preset=top5_and_field",
      { scroll: false }
    );
  });

  it.each([
    ["Working Papers", "working_paper"],
    ["Work in Progress", "work_in_progress"],
  ])("removes the landing journal restriction when switching to %s", async (label, status) => {
    mockFetch(page1);
    const user = userEvent.setup();
    renderWithSWR(<NewsfeedContent />);

    await user.click(screen.getByRole("button", { name: label }));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/publications?page=1&per_page=20&status=${status}&event_type=new_paper`
      );
    });
  });

  it("reads ?tab=publications from URL and activates that tab", async () => {
    window.history.pushState({}, "", "?tab=publications");

    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("event_type=status_change")
      );
    });

    // Restore URL
    window.history.pushState({}, "", "/");
  });

  it("navigates to next page when Next button is clicked", async () => {
    let callCount = 0;
    mockFetch((url: string) => {
      if (url.includes("/api/publications")) {
        callCount++;
        const data = callCount === 1 ? page1 : page2;
        return Promise.resolve({ ok: true, json: async () => data });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() => {
      expect(screen.getByText("Fiscal Policy in Europe")).toBeInTheDocument();
    });

    // Page 1 items replaced by page 2
    expect(screen.queryByText("Immigration and Wages")).not.toBeInTheDocument();
  });
});

describe("feed control regressions", () => {
  it("clears both dates together when All time is selected", async () => {
    window.history.replaceState({}, "", "?tab=all&since=2026-10-01&until=2026-10-10");
    mockFetch(page1);
    const user = userEvent.setup();
    renderWithSWR(<NewsfeedContent />);
    await screen.findByText("Immigration and Wages");
    await user.click(screen.getByRole("button", { name: "Filters (2)" }));
    await user.click(screen.getByRole("button", { name: "All time" }));
    expect(screen.getByLabelText("From date")).toHaveValue("");
    expect(screen.getByLabelText("To date")).toHaveValue("");
    await waitFor(() => expect(global.fetch).toHaveBeenLastCalledWith("/api/publications?page=1&per_page=20"));
    expect(mockReplace).toHaveBeenLastCalledWith("/?tab=all", { scroll: false });
  });

  it.each(["1.5", "-3", "Infinity", "NaN", "9007199254740992"])("uses page 1 for invalid page %s", async (page) => {
    window.history.replaceState({}, "", `?tab=all&page=${page}`);
    mockFetch(page1);
    renderWithSWR(<NewsfeedContent />);
    await screen.findByText("Immigration and Wages");
    expect(global.fetch).toHaveBeenCalledWith("/api/publications?page=1&per_page=20");
  });

  it("recovers an out-of-range bookmark to the returned final page", async () => {
    window.history.replaceState({}, "", "?tab=all&page=9999");
    mockFetch((url: string) => Promise.resolve({ ok: true, json: async () => (
      url.includes("page=9999") ? { ...page2, page: 9999, items: [] } : page2
    ) }));
    renderWithSWR(<NewsfeedContent />);
    await screen.findByText("Fiscal Policy in Europe");
    expect(screen.queryByText(/No updates match/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Previous/ })).toBeInTheDocument();
    expect(mockReplace).toHaveBeenLastCalledWith("/?tab=all&page=2", { scroll: false });
  });

  it("keeps closed filters out of keyboard navigation and traps/restores focus when open", async () => {
    mockFetch(page1);
    const user = userEvent.setup();
    renderWithSWR(<NewsfeedContent />);
    await screen.findByText("Immigration and Wages");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("From date")).not.toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: "Filters (1)" });
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Filters" })).toHaveAttribute("aria-modal", "true");
    const close = screen.getByRole("button", { name: "Close filters" });
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Show 3 results" })).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("does not request researchers until the chooser has a name query", async () => {
    mockFetch(page1);
    const user = userEvent.setup();
    renderWithSWR(<NewsfeedContent />);
    await screen.findByText("Immigration and Wages");
    await user.click(screen.getByRole("button", { name: "Filters (1)" }));
    await user.click(screen.getByRole("button", { name: "Choose researchers" }));
    await user.type(screen.getByRole("searchbox", { name: "Search researchers" }), "Y");
    expect((global.fetch as jest.Mock).mock.calls.some(([url]) => url.includes("researcher-options"))).toBe(false);
  });
});
