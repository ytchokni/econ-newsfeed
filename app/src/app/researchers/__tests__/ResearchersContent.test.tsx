import { render, screen, waitFor } from "@testing-library/react";
import ResearchersContent from "../ResearchersContent";
import type { Researcher } from "@/lib/types";

const researchers: Researcher[] = [
  {
    id: 1,
    first_name: "Max Friedrich",
    last_name: "Steinhardt",
    position: "Professor",
    affiliation: "Freie Universität Berlin",
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
    urls: [],
    website_url: null,
    publication_count: 5,
    fields: [],
  },
];

beforeEach(() => {
  jest.resetAllMocks();
  global.fetch = jest.fn();
});

describe("ResearchersContent", () => {
  it("renders all researchers", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: researchers }),
    });

    render(<ResearchersContent />);

    await waitFor(() => {
      expect(
        screen.getByText("Max Friedrich Steinhardt")
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
  });

  it("shows loading state", () => {
    (global.fetch as jest.Mock).mockReturnValueOnce(new Promise(() => {}));

    render(<ResearchersContent />);
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });

  it("shows error state", async () => {
    (global.fetch as jest.Mock).mockRejectedValueOnce(
      new Error("Network error")
    );

    render(<ResearchersContent />);

    await waitFor(() => {
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
    });
  });
});
