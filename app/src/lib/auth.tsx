"use client";

import { createContext, useCallback, useContext, useState } from "react";
import { SessionProvider, useSession, signIn } from "next-auth/react";
import type { ReactNode } from "react";

const LoginModalContext = createContext<{
  openLoginModal: () => void;
}>({ openLoginModal: () => {} });

export function useLoginModal() {
  return useContext(LoginModalContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [showLogin, setShowLogin] = useState(false);
  const openLoginModal = useCallback(() => setShowLogin(true), []);
  const closeLoginModal = useCallback(() => setShowLogin(false), []);

  return (
    <SessionProvider>
      <LoginModalContext.Provider value={{ openLoginModal }}>
        {children}
        {showLogin && <LoginModal onClose={closeLoginModal} />}
      </LoginModalContext.Provider>
    </SessionProvider>
  );
}

function LoginModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-[var(--bg-card)] rounded-2xl shadow-2xl w-full max-w-sm mx-4 p-8 relative"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-[var(--text-muted)] hover:text-[var(--text-primary)] text-lg"
        >
          &times;
        </button>
        <h2 className="text-xl font-bold text-[var(--text-primary)] mb-2">
          Stay in the loop
        </h2>
        <p className="text-sm text-[var(--text-muted)] mb-6 leading-relaxed">
          Log in to follow researchers you care about and receive a weekly email
          digest when they publish new work.
        </p>
        <button
          onClick={() => signIn("google")}
          className="w-full flex items-center justify-center gap-3 bg-white text-gray-700 font-semibold text-sm rounded-lg px-4 py-3 border border-gray-200 hover:bg-gray-50 transition-colors"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24">
            <path
              d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
              fill="#4285F4"
            />
            <path
              d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              fill="#34A853"
            />
            <path
              d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A11.96 11.96 0 0 0 0 12c0 1.94.46 3.77 1.28 5.4l3.56-2.77v-.54z"
              fill="#FBBC05"
            />
            <path
              d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
              fill="#EA4335"
            />
          </svg>
          Continue with Google
        </button>
      </div>
    </div>
  );
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
