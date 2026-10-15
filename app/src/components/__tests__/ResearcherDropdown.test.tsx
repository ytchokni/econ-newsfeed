import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { SWRConfig } from "swr";
import ResearcherDropdown from "../ResearcherDropdown";

function Chooser() {
  const [selected, setSelected] = useState<string[]>([]);
  return <ResearcherDropdown selected={selected} onChange={setSelected} />;
}

function renderChooser() {
  return render(<SWRConfig value={{ provider: () => new Map(), shouldRetryOnError: false }}><Chooser /></SWRConfig>);
}

beforeEach(() => { global.fetch = jest.fn(); });

it("loads a capped name search lazily and retains a selected researcher across queries", async () => {
  const researcher = { id: 20928, name: "Youngjin Yun", affiliation: "Hanyang University" };
  (global.fetch as jest.Mock).mockImplementation(async (url: string) => ({ ok: true, json: async () => ({
    items: url.includes("Youngjin") || url.includes("ids=20928") ? [researcher] : []
  }) }));
  const user = userEvent.setup();
  renderChooser();
  expect(global.fetch).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Choose researchers" }));
  expect(global.fetch).not.toHaveBeenCalled();
  const search = screen.getByRole("searchbox");
  await user.type(search, "Youngjin Yun");
  const checkbox = await screen.findByRole("checkbox", { name: /Youngjin Yun/ });
  expect(global.fetch).toHaveBeenCalledWith("/api/researcher-options?search=Youngjin+Yun");
  await user.click(checkbox);
  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith("/api/researcher-options?ids=20928"));
  await user.clear(search);
  await user.type(search, "No match");
  await screen.findByText("No matches");
  expect(screen.getByRole("checkbox", { name: /Youngjin Yun/ })).toBeChecked();
  await user.click(screen.getByRole("checkbox", { name: /Youngjin Yun/ }));
  expect(screen.getByRole("button", { name: "Choose researchers" })).toHaveTextContent("All researchers");
});

it("shows an error rather than a false empty result and closes with Escape", async () => {
  (global.fetch as jest.Mock).mockRejectedValue(new Error("offline"));
  const user = userEvent.setup();
  renderChooser();
  await user.click(screen.getByRole("button", { name: "Choose researchers" }));
  await user.type(screen.getByRole("searchbox"), "Yun");
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load researchers");
  expect(screen.queryByText("No matches")).not.toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Choose researchers" })).toHaveFocus();
});
