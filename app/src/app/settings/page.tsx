"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { signOut } from "next-auth/react";
import { useAuth } from "@/lib/auth";
import { useNotificationPrefs, updateNotificationPrefs } from "@/lib/api";

export default function SettingsPage() {
  const { user, isAuthenticated, isLoading, accessToken } = useAuth();
  const router = useRouter();
  const { data: prefs, mutate } = useNotificationPrefs(accessToken);
  const [saving, setSaving] = useState(false);

  const toggleDigest = useCallback(async () => {
    if (!accessToken || !prefs) return;
    const newValue = !prefs.digest_enabled;
    setSaving(true);
    mutate({ ...prefs, digest_enabled: newValue }, false);
    try {
      await updateNotificationPrefs({ digest_enabled: newValue }, accessToken);
      mutate();
    } catch {
      mutate();
    } finally {
      setSaving(false);
    }
  }, [accessToken, prefs, mutate]);

  if (isLoading) return null;
  if (!isAuthenticated) {
    router.push("/");
    return null;
  }

  return (
    <div className="max-w-lg mx-auto">
      <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-8">
        Account Settings
      </h1>

      <section className="bg-[var(--bg-card)] rounded-xl border border-[var(--border)] p-6 mb-6">
        <h2 className="font-sans text-sm font-semibold uppercase tracking-widest text-[var(--text-muted)] mb-4">
          Profile
        </h2>
        <div className="flex items-center gap-4">
          {user?.image ? (
            <img
              src={user.image}
              alt=""
              className="w-12 h-12 rounded-full"
              referrerPolicy="no-referrer"
            />
          ) : (
            <div className="w-12 h-12 rounded-full bg-[var(--accent)] flex items-center justify-center text-white text-lg font-bold">
              {(user?.name || user?.email || "?")[0].toUpperCase()}
            </div>
          )}
          <div>
            <p className="font-semibold text-[var(--text-primary)]">{user?.name}</p>
            <p className="text-sm text-[var(--text-muted)]">{user?.email}</p>
          </div>
        </div>
      </section>

      <section className="bg-[var(--bg-card)] rounded-xl border border-[var(--border)] p-6 mb-6">
        <h2 className="font-sans text-sm font-semibold uppercase tracking-widest text-[var(--text-muted)] mb-4">
          Notifications
        </h2>
        <div className="flex items-center justify-between">
          <div>
            <p className="font-medium text-[var(--text-primary)]">Weekly digest</p>
            <p className="text-sm text-[var(--text-muted)]">
              Email summary of new publications from researchers you follow
            </p>
          </div>
          <button
            onClick={toggleDigest}
            disabled={saving || !prefs}
            className={`relative w-11 h-6 rounded-full transition-colors ${
              prefs?.digest_enabled
                ? "bg-[var(--accent)]"
                : "bg-gray-300"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${
                prefs?.digest_enabled ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>
      </section>

      <section className="bg-[var(--bg-card)] rounded-xl border border-[var(--border)] p-6">
        <h2 className="font-sans text-sm font-semibold uppercase tracking-widest text-[var(--text-muted)] mb-4">
          Account
        </h2>
        <button
          onClick={() => signOut({ callbackUrl: "/" })}
          className="text-sm text-red-500 hover:text-red-600 font-medium transition-colors"
        >
          Sign out
        </button>
      </section>
    </div>
  );
}
