"use client";

import { useEffect, useState } from "react";
import {
  CheckIcon,
  CircleDashedIcon,
  LoaderCircleIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react";

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

/** What setup does, in order. `stages` are the names setup.status reports while it works on a step. */
const STEPS = [
  { id: "connection", label: "Connection", stages: [] },
  { id: "machine", label: "Machine", stages: [] },
  { id: "docker", label: "Docker", stages: ["docker"] },
  { id: "scripts", label: "Runner scripts", stages: ["scripts"] },
  { id: "image", label: "Solver image", stages: ["image"] },
  { id: "xfoil", label: "XFOIL", stages: ["xfoil"] },
  { id: "openfoam", label: "OpenFOAM and gmsh", stages: ["openfoam"] },
  {
    id: "calculix",
    label: "CalculiX and the Python tools",
    stages: ["calculix", "fea"],
  },
  { id: "video", label: "Flow videos (ParaView, ffmpeg)", stages: ["video"] },
] as const;

/** The order setup.status moves through. */
const STAGE_ORDER = [
  "docker",
  "scripts",
  "image",
  "xfoil",
  "openfoam",
  "calculix",
  "fea",
  "video",
  "done",
];

type StepState = "done" | "active" | "pending" | "failed";

/** Where each step is, given what setup last reported. Connection and machine were checked
 * before setup could start; "checks" (an older name for the tool checks) counts as XFOIL. */
export function stepStates(progress: SetupProgress): Record<string, StepState> {
  const stage = progress.stage === "checks" ? "xfoil" : progress.stage;
  const current = STAGE_ORDER.indexOf(stage);
  const states: Record<string, StepState> = {};
  for (const step of STEPS) {
    if (step.stages.length === 0) {
      states[step.id] = "done";
      continue;
    }
    const indexes = step.stages.map((name) => STAGE_ORDER.indexOf(name));
    const first = Math.min(...indexes);
    const last = Math.max(...indexes);
    if (progress.state === "completed" || current > last)
      states[step.id] = "done";
    else if (current >= first)
      states[step.id] = progress.state === "failed" ? "failed" : "active";
    else states[step.id] = "pending";
  }
  // Setup that hasn't reported a stage yet is starting its first step.
  if (current < 0 && progress.state === "running") states.docker = "active";
  return states;
}

function StepIcon({ state }: { state: StepState }) {
  if (state === "done")
    return <CheckIcon className="mt-0.5 size-4 shrink-0 text-green-600" />;
  if (state === "active")
    return <LoaderCircleIcon className="mt-0.5 size-4 shrink-0 animate-spin" />;
  if (state === "failed")
    return <XIcon className="mt-0.5 size-4 shrink-0 text-destructive" />;
  return (
    <CircleDashedIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
  );
}

/** The steps of a setup in progress: a spinner on the one being worked on, a check mark on each
 * one that's done. Shown in the settings, so the progress is visible with the dialog closed. */
export function SetupSteps({ progress }: { progress: SetupProgress }) {
  const states = stepStates(progress);
  return (
    <ul
      className="grid gap-1.5 rounded-lg border p-3 text-sm"
      aria-label="Setup steps"
    >
      {STEPS.map((step) => {
        const state = states[step.id];
        return (
          <li
            key={step.id}
            className="flex items-start gap-2"
            data-state={state}
          >
            <StepIcon state={state} />
            <span
              className={
                state === "pending" ? "text-muted-foreground" : undefined
              }
            >
              {step.label}
              {state === "active" && progress.message && (
                <span className="block text-muted-foreground">
                  {progress.message}
                </span>
              )}
              {state === "failed" && (
                <span className="block break-words text-destructive">
                  {progress.message}
                </span>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function message(failure: unknown, fallback: string) {
  return failure instanceof Error ? failure.message : fallback;
}

/** Installs Docker and the solver image on the connected machine, in the background on the
 * machine. The progress comes from the settings panel, which keeps watching it after this is
 * closed. */
export function WorkstationSetup({
  username,
  disabled,
  progress,
  onProgress,
  autoOpen = false,
}: {
  username: string;
  disabled?: boolean;
  progress: SetupProgress | null;
  onProgress: (progress: SetupProgress) => void;
  /** Starts with the dialog open, for setup that was started when the machine was connected. */
  autoOpen?: boolean;
}) {
  const [open, setOpen] = useState(autoOpen);
  const [plan, setPlan] = useState<SetupPlan | null>(null);
  const [password, setPassword] = useState("");
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const running = progress?.state === "running";

  // What setup would install, wanted whenever it could be started (not while it's running).
  const known = progress !== null;
  useEffect(() => {
    if (!open || running || plan || !known) return;
    let current = true;
    api.setupPlan().then(
      (result) => current && setPlan(result),
      () => undefined,
    );
    return () => {
      current = false;
    };
  }, [open, running, plan, known]);

  async function load() {
    setError(null);
    try {
      onProgress(await api.setupProgress());
    } catch (failure) {
      setError(message(failure, "Couldn't reach the machine."));
    }
  }

  function openDialog() {
    setOpen(true);
    void load();
  }

  async function cancel() {
    setCancelling(true);
    setError(null);
    try {
      onProgress(await api.cancelSetup());
      setPlan(null);
    } catch (failure) {
      setError(message(failure, "Couldn't cancel setup."));
    } finally {
      setCancelling(false);
    }
  }

  async function start() {
    setStarting(true);
    setError(null);
    try {
      onProgress(await api.startSetup(plan?.needs_password ? password : null));
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
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Set up this machine</DialogTitle>
            <DialogDescription>
              Icarus runs its solvers in a Docker container on the machine.
              Setup runs on the machine itself, so you can close this and come
              back; the steps keep updating in the settings.
            </DialogDescription>
          </DialogHeader>

          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {!progress && !error && <Skeleton className="h-24 rounded-lg" />}

          {progress?.state === "cancelled" && (
            <p className="text-sm text-muted-foreground">{progress.message}</p>
          )}
          {progress &&
            progress.state !== "idle" &&
            progress.state !== "cancelled" && (
              <div className="grid gap-3">
                <SetupSteps progress={progress} />
                {progress.log_tail && (
                  <pre
                    aria-label="Setup log"
                    className="h-80 overflow-auto rounded-md bg-muted p-3 font-mono text-xs break-all whitespace-pre-wrap"
                  >
                    {progress.log_tail}
                  </pre>
                )}
              </div>
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
            {running && (
              <Button
                variant="outline"
                disabled={cancelling}
                onClick={() => void cancel()}
              >
                {cancelling ? "Cancelling…" : "Cancel"}
              </Button>
            )}
            {progress?.state !== "completed" && !running && (
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
