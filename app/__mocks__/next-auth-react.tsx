import React from "react";

function useSession() {
  return {
    data: null as null,
    status: "unauthenticated" as const,
    update: () => Promise.resolve(null),
  };
}

const signIn = jest.fn();
const signOut = jest.fn();

function SessionProvider({ children }: { children: React.ReactNode }) {
  return React.createElement(React.Fragment, null, children);
}

export { useSession, signIn, signOut, SessionProvider };
