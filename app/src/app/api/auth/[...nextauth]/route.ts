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
