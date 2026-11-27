import CredentialsProvider from "next-auth/providers/credentials";
import { authorizeEmailCode, EMAIL_CODE_PROVIDER_ID } from "@/lib/email-code-auth";
import NextAuth from "next-auth";
import GoogleProvider from "next-auth/providers/google";
import { encodeBackendJwt } from "@/lib/backend-jwt";

const NEXTAUTH_SECRET = process.env.NEXTAUTH_SECRET || "";

const handler = NextAuth({
  secret: NEXTAUTH_SECRET,
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID || "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET || "",
    }),
    // Passwordless login: the backend emails a one-time code and checks it.
    CredentialsProvider({
      id: EMAIL_CODE_PROVIDER_ID,
      name: "Email code",
      credentials: {
        email: { label: "Email", type: "email" },
        code: { label: "Code", type: "text" },
      },
      authorize: (credentials, req) => authorizeEmailCode(credentials, req?.headers),
    }),
  ],
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account && profile) {
        token.sub = profile.sub;
        token.email = profile.email;
        token.name = profile.name;
        token.picture = (profile as { picture?: string }).picture;
      }
      if (account?.provider === "google" && profile) {
        // Lets the backend attach an existing email-code account to this login.
        token.email_verified =
          (profile as { email_verified?: boolean }).email_verified === true;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        (session.user as { id?: string }).id = token.sub;
        (session.user as { accessToken?: string }).accessToken =
          encodeBackendJwt(token, NEXTAUTH_SECRET);
      }
      return session;
    },
  },
});

export { handler as GET, handler as POST };
