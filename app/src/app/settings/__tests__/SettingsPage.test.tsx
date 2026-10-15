import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "../page";
import { useCapabilities, useNotificationPrefs, updateNotificationPrefs } from "@/lib/api";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ useAuth: () => ({
  user: { name: "Jane", email: "jane@example.test" }, isAuthenticated: true, isLoading: false, accessToken: "test-token"
}) }));
jest.mock("@/lib/api", () => ({ useCapabilities: jest.fn(), useNotificationPrefs: jest.fn(), updateNotificationPrefs: jest.fn() }));
const prefs = { digest_enabled: true, last_digest_sent: null };
const mutate = jest.fn();
beforeEach(() => {
  jest.resetAllMocks();
  (useNotificationPrefs as jest.Mock).mockReturnValue({ data: prefs, mutate });
});

it.each([
  { data: undefined },
  { data: { weekly_digest_available: false } },
  { data: { weekly_digest_available: true }, error: new Error("offline") },
])("disables delivery when capability is unavailable or unknown", (capability) => {
  (useCapabilities as jest.Mock).mockReturnValue(capability);
  render(<SettingsPage />);
  expect(screen.getByRole("switch", { name: "Weekly digest" })).toBeDisabled();
  expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  expect(screen.getByText(/temporarily unavailable/)).toBeInTheDocument();
  expect(updateNotificationPrefs).not.toHaveBeenCalled();
});

it("reports a failed preference save and restores the prior preference", async () => {
  (useCapabilities as jest.Mock).mockReturnValue({ data: { weekly_digest_available: true } });
  (updateNotificationPrefs as jest.Mock).mockRejectedValue(new Error("provider unavailable"));
  const user = userEvent.setup();
  render(<SettingsPage />);
  await user.click(screen.getByRole("switch"));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not save"));
  expect(mutate).toHaveBeenLastCalledWith(prefs, false);
});
