"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { cn } from "cn"

import { Button } from "@/components/ui/button"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldSeparator,
} from "@/components/ui/field"
import { PasswordInput } from "@/components/auth/password-input"
import { SocialButtons } from "@/components/auth/social-buttons"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { useAuth } from "@/stores/auth-store"

/** What the server tells the login page when signing in with Google didn't work (?error=). */
const SIGN_IN_ERRORS: Record<string, string> = {
  google_denied: "Google sign-in was cancelled.",
  google_failed: "Couldn't sign in with Google. Try again.",
  google_unverified: "Google says that email address isn't verified.",
  google_disabled: "This account is disabled.",
  google_unavailable: "Google sign-in isn't set up on this server.",
}

export function LoginForm({
  className,
  signInError,
  ...props
}: React.ComponentProps<"form"> & { signInError?: string }) {
  const router = useRouter()
  const [error, setError] = useState<string | null>(
    signInError ? (SIGN_IN_ERRORS[signInError] ?? "Couldn't sign in.") : null
  )
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault() // never let the browser put the password in the URL
    const form = new FormData(event.currentTarget)
    setError(null)
    setSubmitting(true)
    try {
      const user = await api.login({
        email: String(form.get("email")),
        password: String(form.get("password")),
      })
      useAuth.setState({ user, status: "ready" }) // so the chat doesn't think nobody is signed in
      router.push("/") // the chat
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Couldn't log in.")
      setSubmitting(false)
    }
  }

  return (
    <form
      className={cn("flex flex-col gap-6", className)}
      onSubmit={onSubmit}
      {...props}
    >
      <FieldGroup>
        <div className="flex flex-col items-center gap-1 text-center">
          <h1 className="text-2xl font-bold">Login to your account</h1>
          <p className="text-sm text-balance text-muted-foreground">
            Enter your email below to login to your account
          </p>
        </div>
        <Field>
          <FieldLabel htmlFor="email">Email</FieldLabel>
          <Input
            id="email"
            name="email"
            autoComplete="email"
            type="email"
            placeholder="m@example.com"
            required
            className="bg-background"
          />
        </Field>
        <Field>
          <FieldLabel htmlFor="password">Password</FieldLabel>
          <PasswordInput id="password" name="password" autoComplete="current-password" required />
        </Field>
        <Field>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" disabled={submitting}>
            {submitting ? "Logging in…" : "Login"}
          </Button>
        </Field>
        <FieldSeparator>Or continue with</FieldSeparator>
        <Field>
          <SocialButtons verb="Login" />
          <FieldDescription className="text-center">
            Don&apos;t have an account?{" "}
            <Link href="/signup" className="underline underline-offset-4">
              Sign up
            </Link>
          </FieldDescription>
        </Field>
      </FieldGroup>
    </form>
  )
}
