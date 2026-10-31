"use client";

import { Button } from "@/components/ui/button";

/** Shown when the API can't be reached, instead of mistaking that for being signed out. */
export function ServerUnreachable({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-3 p-6 text-center">
      <p className="text-sm text-muted-foreground">
        Couldn&apos;t reach the Icarus server. Your session is kept; try again in a moment.
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}
