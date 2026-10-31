"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/stores/auth-store";

/**
 * Takes someone who is already signed in off the login and signup pages, into the app. The
 * proxy does this before the page is sent when it can see the session cookie; this covers the
 * rest (an API on another site) by asking the API.
 */
export function SignedInRedirect() {
  const router = useRouter();
  useEffect(() => {
    void useAuth
      .getState()
      .load()
      .then(() => {
        if (useAuth.getState().user) router.replace("/");
      });
  }, [router]);
  return null;
}
