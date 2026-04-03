"use client";

import { useState } from "react";

interface LoginFormProps {
  onSuccess: () => void;
}

export default function LoginForm({ onSuccess }: LoginFormProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (!res.ok) {
        setError("Invalid password");
        return;
      }
      onSuccess();
    } catch {
      setError("Connection error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-8"
      >
        <h1 className="text-xl font-semibold text-zinc-100 mb-6 font-[family-name:var(--font-dm-sans)]">
          Admin Dashboard
        </h1>
        <label className="block text-sm text-zinc-400 mb-2 font-[family-name:var(--font-dm-sans)]">
          Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full px-3 py-2 bg-[#0f1117] border border-[#2a2d3a] rounded text-zinc-100 text-sm focus:outline-none focus:border-[#4a9eff] font-[family-name:var(--font-dm-sans)]"
          autoFocus
        />
        {error && (
          <p className="mt-2 text-sm text-red-400 font-[family-name:var(--font-dm-sans)]">{error}</p>
        )}
        <button
          type="submit"
          disabled={loading || !password}
          className="mt-4 w-full py-2 bg-[#4a9eff] text-white text-sm font-medium rounded hover:bg-[#3a8eef] disabled:opacity-50 disabled:cursor-not-allowed font-[family-name:var(--font-dm-sans)]"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
