import Link from "next/link"
import { FeatherIcon } from "lucide-react"

import { FluidCanvas } from "./fluid-canvas"

/** The frame of the login and signup pages: the form on the left, a fluid to stir on the right. */
export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div className="flex flex-col gap-4 p-6 md:p-10">
        <div className="flex justify-center gap-2 md:justify-start">
          <Link href="/" className="flex items-center gap-2 font-medium">
            <div className="flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <FeatherIcon className="size-4" />
            </div>
            Icarus
          </Link>
        </div>
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-xs">{children}</div>
        </div>
      </div>
      <div className="relative hidden overflow-hidden bg-neutral-950 lg:block">
        <FluidCanvas className="absolute inset-0 size-full" />
        <p className="pointer-events-none absolute inset-x-0 bottom-8 text-center text-sm text-neutral-500 select-none">
          Move your mouse through the flow
        </p>
      </div>
    </div>
  )
}
