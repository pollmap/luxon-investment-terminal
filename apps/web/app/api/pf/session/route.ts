import { NextResponse } from "next/server";

import { auth } from "../../../../auth";
import { authIsRequired, createPfSessionCookie, emailIsAllowed } from "../../../../lib/pf-session";

export async function GET() {
  if (!authIsRequired()) {
    return NextResponse.json({
      auth_required: false,
      authenticated: true,
      email: null
    });
  }

  const session = await auth();
  const email = session?.user?.email ?? null;
  if (!emailIsAllowed(email)) {
    return NextResponse.json(
      {
        auth_required: true,
        authenticated: false,
        email
      },
      { status: 401 }
    );
  }
  const allowedEmail = email;
  if (!allowedEmail) {
    return NextResponse.json(
      {
        auth_required: true,
        authenticated: false,
        email: null
      },
      { status: 401 }
    );
  }

  const response = NextResponse.json({
    auth_required: true,
    authenticated: true,
    email: allowedEmail
  });
  response.cookies.set("pf_session", createPfSessionCookie(allowedEmail), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 8 * 60 * 60
  });
  return response;
}
