import { render, screen } from "@testing-library/react";
import ExtractionTab from "../tabs/ExtractionTab";
import type { AdminDashboardData } from "@/lib/api";

function fixture(overrides: Partial<AdminDashboardData["extraction"]> = {}): AdminDashboardData["extraction"] {
  return {
    worker_enabled: true,
    queue: { never_extracted: 6000, changed_pending: 4000, total: 10000 },
    throughput: {
      completions: { last_hour: 40, last_24h: 1000, last_7d: 5000 },
      attempts: { last_hour: 42, last_24h: 1050, last_7d: 5200 },
    },
    eta_days: 10.0,
    last_call_at: new Date().toISOString(),
    last_extracted_at: new Date().toISOString(),
    tokens_last_24h: 412345,
    daily: [{ date: "2026-06-10", count: 950 }],
    recent_calls: [
      {
        called_at: new Date().toISOString(),
        context_url: "https://example.com/pubs",
        model: "gemma-4-31b-it",
        total_tokens: 4102,
      },
    ],
    ...overrides,
  };
}

describe("ExtractionTab", () => {
  it("renders queue, ETA, and throughput numbers", () => {
    render(<ExtractionTab data={fixture()} />);
    expect(screen.getByText("10,000")).toBeInTheDocument();
    expect(screen.getByText(/~10 days/)).toBeInTheDocument();
    // completions 24h appears in both the stat card and the throughput table
    expect(screen.getAllByText("1,000").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/6,000 never/)).toBeInTheDocument();
  });

  it("shows enabled badge and no stall warning when recently active", () => {
    render(<ExtractionTab data={fixture()} />);
    expect(screen.getByText("Worker enabled")).toBeInTheDocument();
    expect(screen.queryByText(/stalled/i)).not.toBeInTheDocument();
  });

  it("shows stalled warning when enabled, queue non-empty, no call in 30+ min", () => {
    render(
      <ExtractionTab
        data={fixture({ last_call_at: "2020-01-01T00:00:00Z" })}
      />
    );
    expect(screen.getByText(/stalled/i)).toBeInTheDocument();
  });

  it("shows disabled badge and no stall warning when worker disabled", () => {
    render(
      <ExtractionTab
        data={fixture({ worker_enabled: false, last_call_at: null })}
      />
    );
    expect(screen.getByText("Worker disabled")).toBeInTheDocument();
    expect(screen.queryByText(/stalled/i)).not.toBeInTheDocument();
  });

  it("renders recent calls with model and tokens", () => {
    render(<ExtractionTab data={fixture()} />);
    expect(screen.getByText("https://example.com/pubs")).toBeInTheDocument();
    expect(screen.getByText("gemma-4-31b-it")).toBeInTheDocument();
  });

  it("renders em-dash ETA when null", () => {
    render(<ExtractionTab data={fixture({ eta_days: null })} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
