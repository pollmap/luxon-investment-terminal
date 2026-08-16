import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

import { authIsConfigured, authIsRequired, emailIsAllowed } from "./lib/pf-session";

const providers = authIsConfigured()
  ? [
      GitHub({
        clientId: process.env.AUTH_GITHUB_ID,
        clientSecret: process.env.AUTH_GITHUB_SECRET
      })
    ]
  : [];

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  session: { strategy: "jwt" },
  providers,
  callbacks: {
    async signIn({ user, profile }) {
      if (!authIsRequired()) {
        return true;
      }
      const email = user.email ?? ("email" in (profile ?? {}) ? String(profile?.email) : null);
      return emailIsAllowed(email);
    },
    async session({ session, token }) {
      if (session.user && token.email) {
        session.user.email = token.email;
      }
      return session;
    },
    async authorized({ auth: session }) {
      if (!authIsRequired()) {
        return true;
      }
      return emailIsAllowed(session?.user?.email);
    }
  }
});
