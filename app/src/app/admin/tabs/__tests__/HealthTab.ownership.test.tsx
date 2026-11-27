import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SWRConfig } from "swr";
import { DeactivatedUrlsSection } from "../HealthTab";

const flagged = [{
  id: 8, url: "https://sites.google.com/view/institutionaleconomist", page_type: "research",
  ownership_flag: "owner_not_named", ownership_flagged_at: null,
  researcher_name: "Christopher A. Hartwell", researcher_id: 77,
}];

function renderSection() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), shouldRetryOnError: false, dedupingInterval: 0 }}>
      <DeactivatedUrlsSection />
    </SWRConfig>,
  );
}

it("lists pages that never name their owner and confirms one", async () => {
  let reviewList = flagged;
  const fetchMock = jest.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/admin/confirm-url-owner/8" && init?.method === "POST") {
      reviewList = [];
      return { ok: true, status: 200, json: async () => ({ url: flagged[0].url, papers_credited: 4 }) };
    }
    const body = url === "/api/admin/ownership-review-urls" ? reviewList : [];
    return { ok: true, status: 200, json: async () => body };
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  const user = userEvent.setup();
  renderSection();

  expect(await screen.findByText(flagged[0].url)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Confirm owner's page" }));

  expect(fetchMock).toHaveBeenCalledWith("/api/admin/confirm-url-owner/8", { method: "POST" });
  await waitFor(() => expect(screen.queryByText(flagged[0].url)).not.toBeInTheDocument());
});
