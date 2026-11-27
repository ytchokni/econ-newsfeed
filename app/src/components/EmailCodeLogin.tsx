"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { signIn } from "next-auth/react";
import { EmailLoginRequestError, requestEmailLoginCode } from "@/lib/api";
import { EMAIL_CODE_PROVIDER_ID } from "@/lib/email-code-auth";

const VERIFY_ERRORS: Record<string, string> = {
  invalid_code: "That code is incorrect or no longer valid.",
  code_expired: "This code has expired. Request a new one.",
  too_many_attempts: "Too many incorrect attempts. Request a new code.",
  invalid_email: "Enter a valid email address.",
  rate_limit_exceeded: "Too many attempts. Wait a minute and try again.",
  email_login_unavailable: "Email sign-in is not available right now.",
};
const GENERIC_VERIFY_ERROR = "We couldn't sign you in. Try again in a moment.";

const inputClass =
  "w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/40";
const primaryButtonClass =
  "w-full rounded-lg bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50";
const linkButtonClass =
  "text-[var(--link)] hover:underline disabled:text-[var(--text-muted)] disabled:no-underline";

/** Email → one-time code → signed in. Rendered inside the login modal. */
export function EmailCodeLogin({ onSignedIn }: { onSignedIn: () => void }) {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const codeInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  useEffect(() => {
    if (step === "code") codeInput.current?.focus();
  }, [step]);

  async function sendCode(): Promise<boolean> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await requestEmailLoginCode(email.trim());
      setCooldown(result.resend_after);
      return true;
    } catch (e) {
      const err = e instanceof EmailLoginRequestError ? e : null;
      setError(err?.message ?? "Something went wrong. Try again in a moment.");
      if (err?.code === "resend_too_soon" && err.retryAfter) setCooldown(err.retryAfter);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function onSubmitEmail(e: FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    if (await sendCode()) {
      setCode("");
      setStep("code");
    }
  }

  async function onResend() {
    if (await sendCode()) {
      setCode("");
      setNotice("We sent a new code. Earlier codes no longer work.");
    }
  }

  async function onSubmitCode(e: FormEvent) {
    e.preventDefault();
    const trimmed = code.replace(/\s+/g, "");
    if (!/^\d{6}$/.test(trimmed)) {
      setError("Enter the 6-digit code from the email.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await signIn(EMAIL_CODE_PROVIDER_ID, {
        email: email.trim(),
        code: trimmed,
        redirect: false,
      });
      if (result && !result.error) {
        onSignedIn();
        return;
      }
      setError(VERIFY_ERRORS[result?.error ?? ""] ?? GENERIC_VERIFY_ERROR);
    } catch {
      setError(GENERIC_VERIFY_ERROR);
    } finally {
      setBusy(false);
    }
  }

  function useDifferentEmail() {
    setStep("email");
    setCode("");
    setError(null);
    setNotice(null);
  }

  const errorLine = error && (
    <p role="alert" className="text-sm text-[var(--accent)]">
      {error}
    </p>
  );

  if (step === "email") {
    return (
      <form onSubmit={onSubmitEmail} className="space-y-3" noValidate>
        <label htmlFor="email-login-address" className="block text-sm font-medium text-[var(--text-primary)]">
          Email address
        </label>
        <input
          id="email-login-address"
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@university.edu"
          className={inputClass}
        />
        {errorLine}
        <button type="submit" disabled={busy || !email.trim()} className={primaryButtonClass}>
          {busy ? "Sending…" : "Email me a sign-in code"}
        </button>
        <p className="text-xs text-[var(--text-muted)]">
          No password needed. We&apos;ll create your account the first time you sign in.
        </p>
      </form>
    );
  }

  return (
    <form onSubmit={onSubmitCode} className="space-y-3" noValidate>
      <p className="text-sm text-[var(--text-secondary)]">
        We sent a 6-digit code to <span className="font-semibold text-[var(--text-primary)]">{email.trim()}</span>.
        It expires in 10 minutes.
      </p>
      <label htmlFor="email-login-code" className="block text-sm font-medium text-[var(--text-primary)]">
        Sign-in code
      </label>
      <input
        id="email-login-code"
        ref={codeInput}
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={7}
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ""))}
        placeholder="123456"
        className={`${inputClass} tracking-[0.3em] font-mono`}
      />
      {errorLine}
      {notice && !error && <p className="text-sm text-[var(--text-secondary)]">{notice}</p>}
      <button type="submit" disabled={busy || !code.trim()} className={primaryButtonClass}>
        {busy ? "Checking…" : "Sign in"}
      </button>
      <div className="flex items-center justify-between text-sm">
        <button type="button" onClick={useDifferentEmail} className={linkButtonClass}>
          Use a different email
        </button>
        <button type="button" onClick={onResend} disabled={busy || cooldown > 0} className={linkButtonClass}>
          {cooldown > 0 ? `Resend code in ${cooldown}s` : "Resend code"}
        </button>
      </div>
    </form>
  );
}
