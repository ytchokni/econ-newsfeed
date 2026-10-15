import { act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import FilterDrawer from "../FilterDrawer";

const noop = () => {};

function Filters() {
  const [open, setOpen] = useState(false);
  return <>
    <button onClick={() => setOpen(true)}>Filters</button>
    <FilterDrawer
      open={open} onClose={() => setOpen(false)}
      presets={[]} activePreset={undefined} onPresetClick={noop}
      datePreset={null} onDatePreset={noop}
      since={undefined} until={undefined}
      onSinceChange={noop} onUntilChange={noop} onAllTime={noop}
      institutionOptions={[]} selectedInstitutions={[]} onInstitutionChange={noop}
      jelOptions={[]} selectedJelCodes={[]} onJelChange={noop}
      selectedResearchers={[]} onResearcherChange={noop}
      onResetAll={noop} totalResults={0} minDate="2026-06-15"
    />
  </>;
}

it("lets Escape close the researcher menu before the drawer with a document React root", async () => {
  // Next's document root delegates React events to the same node as the
  // drawer's native keyboard listener. A div root would miss this regression.
  const root = createRoot(document);
  try {
    await act(async () => {
      root.render(<html><head /><body><Filters /></body></html>);
    });
    const user = userEvent.setup();
    const screen = within(document.body);
    const trigger = screen.getByRole("button", { name: "Filters" });
    await user.click(trigger);
    const chooser = screen.getByRole("button", { name: "Choose researchers" });
    await user.click(chooser);
    expect(screen.getByRole("searchbox", { name: "Search researchers" })).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("searchbox", { name: "Search researchers" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeInTheDocument();
    expect(chooser).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  } finally {
    await act(async () => root.unmount());
  }
});
