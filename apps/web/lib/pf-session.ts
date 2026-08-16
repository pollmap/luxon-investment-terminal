import { createHmac } from "node:crypto";

const SESSION_TTL_SECONDS = 8 * 60 * 60;

export type PfSessionPayload = {
  email: string;
  iat: number;
  exp: number;
};

export function authIsConfigured() {
  return Boolean(
    process.env.AUTH_SECRET &&
    process.env.AUTH_GITHUB_ID &&
    process.env.AUTH_GITHUB_SECRET &&
    process.env.AUTH_ALLOWED_EMAILS
  );
}

export function authIsRequired() {
  return process.env.AUTH_REQUIRED === "true" || (process.env.VERCEL === "1" && authIsConfigured());
}

export function allowedEmails() {
  return new Set(
    (process.env.AUTH_ALLOWED_EMAILS ?? "")
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean)
  );
}

export function emailIsAllowed(email: string | null | undefined) {
  if (!authIsRequired()) {
    return true;
  }
  if (!email) {
    return false;
  }
  return allowedEmails().has(email.toLowerCase());
}

export function createPfSessionCookie(email: string, now = Math.floor(Date.now() / 1000)) {
  const secret = process.env.PF_COOKIE_SECRET || process.env.AUTH_SECRET;
  if (!secret) {
    throw new Error("PF_COOKIE_SECRET or AUTH_SECRET is required when auth is enabled");
  }
  const payload: PfSessionPayload = {
    email: email.toLowerCase(),
    iat: now,
    exp: now + SESSION_TTL_SECONDS
  };
  const payloadPart = base64UrlEncode(JSON.stringify(payload));
  const signature = createHmac("sha256", secret).update(payloadPart).digest("base64url");
  return `v1.${payloadPart}.${signature}`;
}

function base64UrlEncode(value: string) {
  return Buffer.from(value, "utf8").toString("base64url");
}
