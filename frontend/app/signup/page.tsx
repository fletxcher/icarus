import type { Metadata } from "next"

import { AuthLayout } from "@/components/auth/auth-layout"
import { SignupForm } from "@/components/signup-form"

export const metadata: Metadata = { title: "Sign up · Icarus" }

export default function SignupPage() {
  return (
    <AuthLayout>
      <SignupForm />
    </AuthLayout>
  )
}
