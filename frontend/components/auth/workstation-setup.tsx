"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LoaderCircleIcon, WrenchIcon } from "lucide-react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type SetupPlan, type SetupProgress } from "@/lib/api";

const POLL_MS = 3000;

const STAGES: Record<string, string> = {
  docker: "Docker",
  scripts: "Runner files",
  image: "Solver image",
  checks: "Checking the solvers",
  done: "Done",
};

function message(failure: unknown, fallback: string) {
  return failure instanceof Error ? failure.message : fallback;
}

function Progress({ progress }: { progress: SetupProgress }) {
  const failed = progress.state === "failed";
  return (
    <div className="grid gap-3 text-sm" aria-live="polite">
      <div className="flex items-start gap-2">
        {progress.state === "running" && (
          <LoaderCircleIcon className="mt-0.5 size-4 shrink-0 animate-spin" />
        )}
        <span className={failed ? "text-destructive" : undefined}>
          {STAGES[progress.stage] ?? "Starting"}
          {progress.message && (
            <span className="block text-muted-foreground">
              {progress.message}
            </span>
          )}
        </span>
      </div>
      {progress.log_tail && (
        <pre
          aria-label="Setup log"
          className="max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap break-all"
        >
          {progress.log_tail}
        </pre>
      )}
    </div>
  );
}

/** Installs Docker and the solver image on the saved machine, in the background on the machine:
 * the person can close this and come back, and the progress is picked up again. */
export function WorkstationSetup({
  username,
  disabled,
  onFinished,
  autoOpen = false,
  initialProgress = null,
}: {
  username: string;
  disabled?: boolean;
  onFinished: () => void;
  /** Starts with the dialog open, for setup that was started when the machine was connected. */
  autoOpen?: boolean;
  initialProgress?: SetupProgress | null;
}) {
  const [open, setOpen] = useState(autoOpen);
  const [plan, setPlan] = useState<SetupPlan | null>(null);
  const [progress, setProgress] = useState<SetupProgress | null>(initialProgress);
  const [password, setPassword] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const finished = useRef(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const current = await api.setupProgress();
      setProgress(current);
      if (current.state !== "running") setPlan(await api.setupPlan());
    } catch (failure) {
      setError(message(failure, "Couldn't reach the machine."));
    }
  }, []);

  function openDialog() {
    finished.current = false;
    setOpen(true);
    void load();
  }

  const running = progress?.state === "running";
  useEffect(() => {
    if (!open || !running) return;
    const timer = setInterval(() => {
      api
        .setupProgress()
        .then((current) => {
          setProgress(current);
          // Setup that started by itself hasn't loaded its plan; a retry needs it.
          if (current.state !== "running" && !plan) {
            void api.setupPlan().then(setPlan, () => undefined);
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [open, running, plan]);

  useEffect(() => {
    if (progress?.state === "completed" && !finished.current) {
      finished.current = true;
      toast.success("The machine is ready");
      onFinished();
    }
  }, [progress?.state, onFinished]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      setProgress(await api.startSetup(plan?.needs_password ? password : null));
    } catch (failure) {
      setError(message(failure, "Couldn't start setup."));
    } finally {
      // The password only lives in this form until the request is sent.
      setPassword("");
      setStarting(false);
    }
  }

  const askPassword = plan?.needs_password && !running;
  const canStart =
    !!plan?.can_run && !starting && !running && (!askPassword || !!password);

  return (
    <>
      <Button variant="outline" disabled={disabled} onClick={openDialog}>
        <WrenchIcon />
        Set up this machine
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Set up this machine</DialogTitle>
            <DialogDescription>
              Icarus runs its solvers in a Docker container on the machine.
              Setup runs on the machine itself, so you can close this and come
              back.
            </DialogDescription>
          </DialogHeader>

          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {!progress && !error && <Skeleton className="h-24 rounded-lg" />}

          {progress && progress.state !== "idle" && (
            <Progress progress={progress} />
          )}

          {plan && !running && progress?.state !== "completed" && (
            <div className="grid gap-3 text-sm">
              {plan.can_run ? (
                <>
                  <p>This will install:</p>
                  <ul className="list-disc space-y-1 pl-5">
                    {plan.installs.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p role="alert" className="text-destructive">
                  {plan.reason}
                </p>
              )}
              {askPassword && plan.can_run && (
                <div className="grid gap-1.5">
                  <Label htmlFor="sudo-password">
                    Password for {username} on the machine
                  </Label>
                  <PasswordInput
                    id="sudo-password"
                    autoComplete="off"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <p className="text-muted-foreground">
                    Installing Docker takes root. This password is used once for
                    that and is never stored.
                  </p>
                </div>
              )}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              {progress?.state === "completed" ? "Close" : "Cancel"}
            </Button>
            {progress?.state !== "completed" && (
              <Button disabled={!canStart} onClick={() => void start()}>
                {starting
                  ? "Starting…"
                  : progress?.state === "failed"
                    ? "Try again"
                    : "Confirm install"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
