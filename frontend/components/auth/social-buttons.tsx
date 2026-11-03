"use client"

import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"

function GoogleIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"
        fill="#EA4335"
      />
    </svg>
  );
}

/** "Continue with" buttons: Google, once the server has it set up. */
export function SocialButtons({ verb }: { verb: "Login" | "Sign up" }) {
  // null until the server has said; the button is disabled until then and when Google isn't set up.
  const [google, setGoogle] = useState<boolean | null>(null)
  useEffect(() => {
    api
      .providers()
      .then((providers) => setGoogle(providers.google))
      .catch(() => setGoogle(false))
  }, [])

  return (
    <div className="grid gap-2">
      {google ? (
        <Button variant="outline" asChild>
          <a href={api.googleLoginUrl()}>
            <GoogleIcon />
            {verb} with Google
          </a>
        </Button>
      ) : (
        <Button
          variant="outline"
          type="button"
          disabled
          title={google === false ? "Google sign-in isn't set up on this server" : undefined}
        >
          <GoogleIcon />
          {verb} with Google
        </Button>
      )}
    </div>
  )
}
