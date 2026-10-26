import { Button } from "@/components/ui/button"

function GoogleIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
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
  )
}

function AppleIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M16.37 1.43c0 1.14-.42 2.2-1.25 3.17-.99 1.14-2.19 1.8-3.49 1.7a3.5 3.5 0 0 1 1.3-2.9c.9-.8 2.2-1.4 3.44-1.97zM20.5 17.1c-.55 1.27-.82 1.84-1.52 2.96-.99 1.57-2.38 3.52-4.1 3.54-1.53.02-1.93-1-4.01-.99-2.08.01-2.51 1.01-4.04.99-1.72-.02-3.04-1.78-4.03-3.35C-.1 15.9-.4 10.7 1.3 8.1c1.2-1.84 3.1-2.92 4.89-2.92 1.82 0 2.96 1 4.46 1 1.46 0 2.35-1 4.45-1 1.59 0 3.28.87 4.48 2.37-3.94 2.16-3.3 7.78.92 9.55z"
        fill="currentColor"
      />
    </svg>
  )
}

/** "Continue with" buttons. Not wired to an account provider yet. */
export function SocialButtons({ verb }: { verb: "Login" | "Sign up" }) {
  return (
    <div className="grid gap-2">
      <Button variant="outline" type="button">
        <GoogleIcon />
        {verb} with Google
      </Button>
      <Button variant="outline" type="button">
        <AppleIcon />
        {verb} with Apple
      </Button>
    </div>
  )
}
