import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { signIn } from "next-auth/react";
import { AuthProvider, useLoginModal } from "../auth";
import { useCapabilities } from "../api";

jest.mock("../api", () => ({ useCapabilities: jest.fn() }));
function Trigger() {
  const { openLoginModal } = useLoginModal();
  return <button onClick={openLoginModal}>Log in</button>;
}

it.each([undefined, false, true])("only promises a digest when delivery is available (%s)", async (available) => {
  (useCapabilities as jest.Mock).mockReturnValue({ data: available === undefined ? undefined : { weekly_digest_available: available } });
  const user = userEvent.setup();
  render(<AuthProvider><Trigger /></AuthProvider>);
  await user.click(screen.getByRole("button", { name: "Log in" }));
  expect(screen.getByText(/see their updates in My Feed/)).toBeInTheDocument();
  if (available) expect(screen.getByText(/weekly email digest/)).toBeInTheDocument();
  else expect(screen.queryByText(/weekly email digest/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Continue with Google" }));
  expect(signIn).toHaveBeenCalledWith("google");
});
