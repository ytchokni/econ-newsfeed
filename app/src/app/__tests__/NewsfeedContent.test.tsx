import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SWRConfig } from "swr";
import NewsfeedContent from "../NewsfeedContent";
import type { PaginatedResponse, Publication } from "@/lib/types";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
}));

const page1: PaginatedResponse<Publication> = {
  items: [
    {
      id: 1,
      title: "Immigration and Wages",
      authors: [{ id: 1, first_name: "Max", last_name: "Steinhardt" }],
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
      authors: [{ id: 2, first_name: "Jane", last_name: "Doe" }],
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
      authors: [{ id: 1, first_name: "Max", last_name: "Steinhardt" }],
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
});

describe("NewsfeedContent", () => {
  it("renders publications grouped by discovery date", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    });

    expect(screen.getByText("Trade Shocks")).toBeInTheDocument();
    // Check date group headers exist
    expect(screen.getByText(/Mar 15, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Mar 14, 2026/)).toBeInTheDocument();
  });

  it("shows loading skeletons initially", () => {
    (global.fetch as jest.Mock).mockReturnValue(new Promise(() => {}));

    renderWithSWR(<NewsfeedContent />);
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
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

  it("renders New Projects and Status Changes toggle buttons", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /new projects/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /status changes/i })).toBeInTheDocument();
  });

  it("defaults to New Projects tab as active", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    });

    // Verify the API was called with event_type=new_paper
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("event_type=new_paper")
    );
  });

  it("switches to Status Changes tab and resets filters", async () => {
    mockFetch(page1);

    renderWithSWR(<NewsfeedContent />);

    await waitFor(() => {
      expect(screen.getByText("Immigration and Wages")).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /status changes/i }));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("event_type=status_change")
      );
    });
  });

  it("reads ?tab=status_change from URL and activates that tab", async () => {
    // Use pushState to set the URL — jsdom allows this without navigation.
    window.history.pushState({}, "", "?tab=status_change");

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
