import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UnsubscribeContent from "../UnsubscribeContent";
import UnsubscribePage, { metadata } from "../page";

const fetchMock = jest.fn();

function respond(status: number) {
  return Promise.resolve({ status, ok: status < 400 } as Response);
}

beforeEach(() => {
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

it("asks for confirmation and does not unsubscribe on page load", () => {
  render(<UnsubscribeContent token="u2.42.sig" />);
  expect(
    screen.getByRole("heading", { name: "Unsubscribe from the weekly digest?" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Unsubscribe" })).toBeEnabled();
  expect(fetchMock).not.toHaveBeenCalled();
});

it("POSTs the token on confirm, then offers to resubscribe", async () => {
  fetchMock.mockReturnValueOnce(respond(200)).mockReturnValueOnce(respond(200));
  const user = userEvent.setup();
  render(<UnsubscribeContent token="u2.42.a+b/c" />);

  await user.click(screen.getByRole("button", { name: "Unsubscribe" }));
  expect(await screen.findByRole("heading", { name: /unsubscribed/ })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/users/unsubscribe?token=u2.42.a%2Bb%2Fc",
    expect.objectContaining({ method: "POST" }),
  );
  expect(screen.getByRole("link", { name: "Manage email preferences" })).toHaveAttribute(
    "href",
    "/settings",
  );

  await user.click(screen.getByRole("button", { name: "Resubscribe" }));
  expect(await screen.findByRole("heading", { name: /subscribed again/ })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/users/resubscribe?token=u2.42.a%2Bb%2Fc",
    expect.objectContaining({ method: "POST" }),
  );
});

it("shows an invalid-link message when the token is rejected", async () => {
  fetchMock.mockReturnValueOnce(respond(400));
  const user = userEvent.setup();
  render(<UnsubscribeContent token="u2.42.forged" />);
  await user.click(screen.getByRole("button", { name: "Unsubscribe" }));
  expect(await screen.findByRole("heading", { name: /isn.t valid/ })).toBeInTheDocument();
});

it("shows an invalid-link message without a token and makes no request", () => {
  render(<UnsubscribeContent token="" />);
  expect(screen.getByRole("heading", { name: /isn.t valid/ })).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});

it("keeps the confirm button available after a server or network error", async () => {
  fetchMock.mockReturnValueOnce(respond(500)).mockRejectedValueOnce(new Error("offline"));
  const user = userEvent.setup();
  render(<UnsubscribeContent token="u2.42.sig" />);

  await user.click(screen.getByRole("button", { name: "Unsubscribe" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong"));
  await user.click(screen.getByRole("button", { name: "Unsubscribe" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not reach"));
  expect(screen.getByRole("button", { name: "Unsubscribe" })).toBeEnabled();
});

it("explains when resubscribing is unavailable", async () => {
  fetchMock.mockReturnValueOnce(respond(200)).mockReturnValueOnce(respond(503));
  const user = userEvent.setup();
  render(<UnsubscribeContent token="u2.42.sig" />);
  await user.click(screen.getByRole("button", { name: "Unsubscribe" }));
  await user.click(await screen.findByRole("button", { name: "Resubscribe" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("temporarily unavailable"),
  );
  expect(screen.getByRole("heading", { name: /unsubscribed/ })).toBeInTheDocument();
});

it("page passes a single token through and is not indexed", async () => {
  render(await UnsubscribePage({ searchParams: Promise.resolve({ token: "u2.1.x" }) }));
  expect(screen.getByRole("button", { name: "Unsubscribe" })).toBeInTheDocument();
  expect(metadata.robots).toBe("noindex, nofollow");
  expect(metadata.referrer).toBe("no-referrer");
});

it("page treats a repeated token parameter as invalid", async () => {
  render(await UnsubscribePage({ searchParams: Promise.resolve({ token: ["a", "b"] }) }));
  expect(screen.getByRole("heading", { name: /isn.t valid/ })).toBeInTheDocument();
});
