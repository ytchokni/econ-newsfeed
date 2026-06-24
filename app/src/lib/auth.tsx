"use client";

import { SessionProvider, useSession } from "next-auth/react";
import type { ReactNode } from "react";

export function AuthProvider({ children }: { children: ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}

export function useAuth() {
  const { data: session, status } = useSession();
  const user = session?.user as
    | ({ name?: string | null; email?: string | null; image?: string | null } & {
        id?: string;
        accessToken?: string;
      })
    | null;

  return {
    user: user ?? null,
    isAuthenticated: status === "authenticated",
    isLoading: status === "loading",
    accessToken: user?.accessToken ?? null,
  };
}
