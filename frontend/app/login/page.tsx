import type { Metadata } from "next"

import { AuthLayout } from "@/components/auth/auth-layout"
import { LoginForm } from "@/components/login-form"

export const metadata: Metadata = { title: "Log in · Icarus" }

export default function LoginPage() {
  return (
    <AuthLayout>
      <LoginForm />
    </AuthLayout>
  )
}
