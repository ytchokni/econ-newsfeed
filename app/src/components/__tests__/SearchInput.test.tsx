import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import SearchInput from "../SearchInput";

beforeEach(() => {
  jest.useFakeTimers();
});
afterEach(() => {
  jest.useRealTimers();
});

describe("SearchInput", () => {
  it("renders with placeholder", () => {
    render(<SearchInput value="" onChange={jest.fn()} placeholder="Search papers..." />);
    expect(screen.getByPlaceholderText("Search papers...")).toBeInTheDocument();
  });

  it("calls onChange after debounce delay", async () => {
    const onChange = jest.fn();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<SearchInput value="" onChange={onChange} placeholder="Search..." />);

    const input = screen.getByPlaceholderText("Search...");
    await user.type(input, "monetary");

    // Should not have called onChange yet (debounce pending)
    expect(onChange).not.toHaveBeenCalled();

    // Advance past debounce delay (300ms)
    act(() => { jest.advanceTimersByTime(300); });

    expect(onChange).toHaveBeenCalledWith("monetary");
  });

  it("shows clear button when value is non-empty", () => {
    render(<SearchInput value="test" onChange={jest.fn()} placeholder="Search..." />);
    expect(screen.getByRole("button", { name: /clear/i })).toBeInTheDocument();
  });

  it("hides clear button when value is empty", () => {
    render(<SearchInput value="" onChange={jest.fn()} placeholder="Search..." />);
    expect(screen.queryByRole("button", { name: /clear/i })).not.toBeInTheDocument();
  });

  it("calls onChange with empty string when clear is clicked", async () => {
    const onChange = jest.fn();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<SearchInput value="test" onChange={onChange} placeholder="Search..." />);

    await user.type(screen.getByRole("textbox"), " pending");
    await user.click(screen.getByRole("button", { name: /clear/i }));
    act(() => { jest.advanceTimersByTime(300); });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("");
  });

  it.each(["", "replacement"])("cancels pending input when the external value changes to %j", async (nextValue) => {
    const onChange = jest.fn();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    const { rerender } = render(<SearchInput value="test" onChange={onChange} />);

    await user.type(screen.getByRole("textbox"), " pending");
    rerender(<SearchInput value={nextValue} onChange={onChange} />);
    act(() => { jest.advanceTimersByTime(300); });

    expect(screen.getByRole("textbox")).toHaveValue(nextValue);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not restore a removed filter when a pending search is applied", async () => {
    function FilteredSearch() {
      const [filters, setFilters] = useState<{ search: string; preset?: string }>({
        search: "",
        preset: "top5_and_field",
      });
      return (
        <>
          <SearchInput
            value={filters.search}
            onChange={(search) => setFilters({ ...filters, search })}
          />
          {filters.preset && (
            <button onClick={() => setFilters({ ...filters, preset: undefined })}>
              Remove journal filter
            </button>
          )}
          <output>{JSON.stringify(filters)}</output>
        </>
      );
    }

    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<FilteredSearch />);
    await user.type(screen.getByRole("textbox"), "monetary");
    await user.click(screen.getByRole("button", { name: "Remove journal filter" }));
    act(() => { jest.advanceTimersByTime(300); });

    expect(screen.getByRole("status")).toHaveTextContent('{"search":"monetary"}');
    expect(screen.queryByRole("button", { name: "Remove journal filter" })).not.toBeInTheDocument();
  });

  it("cancels pending input on unmount", async () => {
    const onChange = jest.fn();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    const { unmount } = render(<SearchInput value="" onChange={onChange} />);

    await user.type(screen.getByRole("textbox"), "monetary");
    unmount();
    act(() => { jest.advanceTimersByTime(300); });

    expect(onChange).not.toHaveBeenCalled();
  });
});
