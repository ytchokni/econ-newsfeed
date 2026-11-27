"use client";

import { useState } from "react";
import Link from "next/link";

type State =
  | "confirm"
  | "unsubscribing"
  | "unsubscribed"
  | "resubscribing"
  | "resubscribed"
  | "invalid";

async function postDigestToken(action: "unsubscribe" | "resubscribe", token: string): Promise<number> {
  const res = await fetch(`/api/users/${action}?token=${encodeURIComponent(token)}`, {
    method: "POST",
    cache: "no-store",
  });
  return res.status;
}

const primaryButton =
  "inline-flex items-center justify-center rounded-lg bg-[var(--ink)] px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60 transition-opacity";
const secondaryLink =
  "text-sm font-medium text-[var(--link)] hover:underline";

export default function UnsubscribeContent({ token }: { token: string }) {
  const [state, setState] = useState<State>(token ? "confirm" : "invalid");
  const [message, setMessage] = useState<string | null>(null);

  async function unsubscribe() {
    setState("unsubscribing");
    setMessage(null);
    try {
      const status = await postDigestToken("unsubscribe", token);
      if (status === 200) setState("unsubscribed");
      else if (status === 400 || status === 422) setState("invalid");
      else {
        setState("confirm");
        setMessage("Something went wrong. Please try again.");
      }
    } catch {
      setState("confirm");
      setMessage("Could not reach the server. Please try again.");
    }
  }

  async function resubscribe() {
    setState("resubscribing");
    setMessage(null);
    try {
      const status = await postDigestToken("resubscribe", token);
      if (status === 200) setState("resubscribed");
      else {
        setState("unsubscribed");
        setMessage(
          status === 503
            ? "Weekly emails are temporarily unavailable. Please try again later."
            : "Could not resubscribe you. Please try again or use your email preferences.",
        );
      }
    } catch {
      setState("unsubscribed");
      setMessage("Could not reach the server. Please try again.");
    }
  }

  return (
    <div className="max-w-lg mx-auto py-12">
      <section className="bg-[var(--bg-card)] rounded-xl border border-[var(--border)] p-8">
        {(state === "confirm" || state === "unsubscribing") && (
          <>
            <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-3">
              Unsubscribe from the weekly digest?
            </h1>
            <p className="text-[var(--text-secondary)] mb-6">
              You will stop receiving the weekly email about new papers from researchers you
              follow. You will still follow them on Econ Newsfeed.
            </p>
            <div className="flex flex-wrap items-center gap-4">
              <button
                type="button"
                onClick={unsubscribe}
                disabled={state === "unsubscribing"}
                className={primaryButton}
              >
                {state === "unsubscribing" ? "Unsubscribing…" : "Unsubscribe"}
              </button>
              <Link href="/" className={secondaryLink}>
                Keep receiving it
              </Link>
            </div>
          </>
        )}

        {(state === "unsubscribed" || state === "resubscribing") && (
          <>
            <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-3">
              You&rsquo;re unsubscribed
            </h1>
            <p className="text-[var(--text-secondary)] mb-6">
              You won&rsquo;t receive the weekly digest anymore. Changed your mind?
            </p>
            <div className="flex flex-wrap items-center gap-4">
              <button
                type="button"
                onClick={resubscribe}
                disabled={state === "resubscribing"}
                className={primaryButton}
              >
                {state === "resubscribing" ? "Resubscribing…" : "Resubscribe"}
              </button>
              <Link href="/settings" className={secondaryLink}>
                Manage email preferences
              </Link>
            </div>
          </>
        )}

        {state === "resubscribed" && (
          <>
            <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-3">
              You&rsquo;re subscribed again
            </h1>
            <p className="text-[var(--text-secondary)] mb-6">
              The weekly digest will resume with the next issue.
            </p>
            <Link href="/" className={secondaryLink}>
              Back to the newsfeed
            </Link>
          </>
        )}

        {state === "invalid" && (
          <>
            <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-3">
              This unsubscribe link isn&rsquo;t valid
            </h1>
            <p className="text-[var(--text-secondary)] mb-6">
              The link may be incomplete. Sign in and turn off the weekly digest in your email
              preferences instead.
            </p>
            <Link href="/settings" className={secondaryLink}>
              Manage email preferences
            </Link>
          </>
        )}

        {message && (
          <p role="alert" className="mt-4 text-sm text-red-600">
            {message}
          </p>
        )}
      </section>
    </div>
  );
}
