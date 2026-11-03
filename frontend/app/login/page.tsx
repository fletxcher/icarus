import type { Metadata } from "next"

import { AuthLayout } from "@/components/auth/auth-layout"
import { LoginForm } from "@/components/login-form"

export const metadata: Metadata = { title: "Log in · Icarus" }

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>
}) {
  const { error } = await searchParams
  return (
    <AuthLayout>
      <LoginForm signInError={error} />
    </AuthLayout>
  )
}
