import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { signIn } from "next-auth/react";
import { EmailCodeLogin } from "../EmailCodeLogin";
import { EmailLoginRequestError, requestEmailLoginCode } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  requestEmailLoginCode: jest.fn(),
}));

const requestCode = requestEmailLoginCode as jest.Mock;
const mockSignIn = signIn as jest.Mock;

beforeEach(() => {
  jest.useFakeTimers();
  requestCode.mockReset();
  mockSignIn.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

// The countdown re-arms a one-second timer after each render.
function tick(seconds: number) {
  for (let i = 0; i < seconds; i++) {
    act(() => {
      jest.advanceTimersByTime(1_000);
    });
  }
}

function setup() {
  const onSignedIn = jest.fn();
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  render(<EmailCodeLogin onSignedIn={onSignedIn} />);
  return { user, onSignedIn };
}

async function reachCodeStep(user: ReturnType<typeof userEvent.setup>) {
  requestCode.mockResolvedValueOnce({ expires_in: 600, resend_after: 60 });
  await user.type(screen.getByLabelText("Email address"), "  Alice@Example.com ");
  await user.click(screen.getByRole("button", { name: "Email me a sign-in code" }));
  await screen.findByLabelText("Sign-in code");
}

it("sends a code, then signs in with it", async () => {
  const { user, onSignedIn } = setup();
  await reachCodeStep(user);

  expect(requestCode).toHaveBeenCalledWith("Alice@Example.com");
  expect(screen.getByText(/We sent a 6-digit code to/)).toHaveTextContent("Alice@Example.com");
  expect(screen.getByLabelText("Sign-in code")).toHaveFocus();

  mockSignIn.mockResolvedValueOnce({ ok: true, error: null, status: 200, url: "/" });
  await user.type(screen.getByLabelText("Sign-in code"), "123456");
  await user.click(screen.getByRole("button", { name: "Sign in" }));

  expect(mockSignIn).toHaveBeenCalledWith("email-code", {
    email: "Alice@Example.com",
    code: "123456",
    redirect: false,
  });
  expect(onSignedIn).toHaveBeenCalled();
});

it("shows a clear error for a wrong code and stays on the code step", async () => {
  const { user, onSignedIn } = setup();
  await reachCodeStep(user);

  mockSignIn.mockResolvedValueOnce({ ok: true, error: "invalid_code", status: 200, url: null });
  await user.type(screen.getByLabelText("Sign-in code"), "000000");
  await user.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("That code is incorrect or no longer valid.");
  expect(onSignedIn).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Sign-in code")).toBeInTheDocument();
});

it.each([
  ["too_many_attempts", "Too many incorrect attempts. Request a new code."],
  ["code_expired", "This code has expired. Request a new one."],
  ["email_login_paused", "Email sign-in is paused for up to an hour. Try again later or continue with Google."],
  ["something_else", "We couldn't sign you in. Try again in a moment."],
])("maps verify error %s", async (error, message) => {
  const { user } = setup();
  await reachCodeStep(user);
  mockSignIn.mockResolvedValueOnce({ ok: true, error, status: 200, url: null });
  await user.type(screen.getByLabelText("Sign-in code"), "111111");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
});

it("checks the code format before calling NextAuth", async () => {
  const { user } = setup();
  await reachCodeStep(user);
  await user.type(screen.getByLabelText("Sign-in code"), "123");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Enter the 6-digit code from the email.");
  expect(mockSignIn).not.toHaveBeenCalled();
});

it("lets the user resend only after the cooldown", async () => {
  const { user } = setup();
  await reachCodeStep(user);

  expect(screen.getByRole("button", { name: "Resend code in 60s" })).toBeDisabled();
  tick(59);
  expect(screen.getByRole("button", { name: "Resend code in 1s" })).toBeDisabled();
  tick(1);

  requestCode.mockResolvedValueOnce({ expires_in: 600, resend_after: 60 });
  await user.click(screen.getByRole("button", { name: "Resend code" }));
  expect(requestCode).toHaveBeenCalledTimes(2);
  expect(await screen.findByText(/We sent a new code/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Resend code in 60s" })).toBeDisabled();
});

it("shows request errors and stays on the email step", async () => {
  const { user } = setup();
  requestCode.mockRejectedValueOnce(
    new EmailLoginRequestError("Enter a valid email address.", "invalid_email", null),
  );
  await user.type(screen.getByLabelText("Email address"), "nope");
  await user.click(screen.getByRole("button", { name: "Email me a sign-in code" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Enter a valid email address.");
  expect(screen.queryByLabelText("Sign-in code")).not.toBeInTheDocument();
});

it("can go back and use a different email", async () => {
  const { user } = setup();
  await reachCodeStep(user);
  await user.click(screen.getByRole("button", { name: "Use a different email" }));
  expect(screen.getByLabelText("Email address")).toHaveValue("Alice@Example.com");
});
