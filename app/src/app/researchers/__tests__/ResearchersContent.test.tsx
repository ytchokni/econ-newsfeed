import { render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import ResearchersContent from "../ResearchersContent";
import type { Researcher } from "@/lib/types";

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
  },
];

const emptyFilterOptions = {
  institutions: [],
  positions: [],
  fields: [],
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
      return { ok: true, json: async () => researchersResponse };
    }
    return { ok: false, status: 404, statusText: "Not Found" };
  });
}

beforeEach(() => {
  jest.resetAllMocks();
  global.fetch = jest.fn();
});

describe("ResearchersContent", () => {
  it("renders all researchers", async () => {
    mockFetchResponses({ items: researchers });

    renderWithSWR(<ResearchersContent />);

    await waitFor(() => {
      expect(
        screen.getByText("Max Friedrich Steinhardt")
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
  });

  it("shows loading state", () => {
    (global.fetch as jest.Mock).mockReturnValue(new Promise(() => {}));

    renderWithSWR(<ResearchersContent />);
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });

  it("shows error state", async () => {
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
});
