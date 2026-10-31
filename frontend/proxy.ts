import { NextResponse, type NextRequest } from "next/server";

// The backend's session cookie (backend/src/api/auth/router.py).
const SESSION_COOKIE = "icarus_session";
const AUTH_PAGES = new Set(["/login", "/signup"]);

/** Where the backend runs: the same default as lib/api.ts (empty means this very origin). */
const API_URL =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

/**
 * Whether the session cookie reaches this server. Cookies are shared by every port of a host and
 * by a whole origin, so that's the case when the API is on this host (localhost on another
 * port, or the same domain). With the API on another site the cookie is invisible here and the
 * pages decide for themselves, once they've asked the API who is signed in.
 */
function seesSessionCookie(request: NextRequest): boolean {
  if (!API_URL) return true;
  try {
    return new URL(API_URL).hostname === request.nextUrl.hostname;
  } catch {
    return false;
  }
}

/**
 * Sends people where they belong before anything is drawn: a browser holding a session goes
 * straight into the app (a new chat) instead of seeing the login page, and one without goes to
 * log in instead of seeing an empty app. The cookie's presence is all that's checked here; if the
 * session behind it has expired the app finds out when it asks the API, signs the cookie out
 * and returns to the login page.
 */
export function proxy(request: NextRequest) {
  if (!seesSessionCookie(request)) return NextResponse.next();
  const signedIn = request.cookies.has(SESSION_COOKIE);
  const onAuthPage = AUTH_PAGES.has(request.nextUrl.pathname);
  if (signedIn && onAuthPage) return NextResponse.redirect(new URL("/", request.url));
  if (!signedIn && !onAuthPage) return NextResponse.redirect(new URL("/login", request.url));
  return NextResponse.next();
}

export const config = {
  // Pages only: not the framework's files, the favicon or anything with a file extension.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
