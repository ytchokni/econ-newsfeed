import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

    await user.click(screen.getByRole("button", { name: /clear/i }));

    expect(onChange).toHaveBeenCalledWith("");
  });
});
