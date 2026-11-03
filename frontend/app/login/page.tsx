import { Suspense } from "react"
import type { Metadata } from "next"

import { AuthLayout } from "@/components/auth/auth-layout"
import { LoginForm } from "@/components/login-form"

export const metadata: Metadata = { title: "Log in · Icarus" }

export default function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>
}) {
  return (
    <AuthLayout>
      <Suspense fallback={<LoginForm />}>
        <SignInErrorForm searchParams={searchParams} />
      </Suspense>
    </AuthLayout>
  )
}

async function SignInErrorForm({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>
}) {
  const { error } = await searchParams
  return <LoginForm signInError={error} />
}
