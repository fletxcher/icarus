"use client";

import { CircleCheckIcon, CircleXIcon, RefreshCwIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { api, type StatusCheck } from "@/lib/api";

const EXAMPLES = [
  "Plot the lift polar of a NACA 2412 at Re = 1e6.",
  "Simulate flow around a cylinder at Re = 150 and show the vortex shedding.",
  "Compare NACA 0012 and NACA 4412 at 6° angle of attack.",
  "Which airfoils are good for a low-speed glider?",
];

const CAPABILITIES = [
  "Airfoil polars and pressure distributions with XFOIL",
  "NACA airfoil search and comparison",
  "2D CFD (OpenFOAM) around airfoils, cylinders, ellipses, wedges and rectangles, with wall conditions",
  "Stress, deflection and frequencies of an uploaded STL solid (CalculiX), under spin, pressure and heat",
  "Plots, and flow images and video from finished simulations",
];

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ["Ctrl", "K"], label: "Search chats" },
  { keys: ["Ctrl", "Shift", "O"], label: "New chat" },
  { keys: ["Ctrl", "B"], label: "Show or hide the sidebar" },
  { keys: ["Ctrl", "Shift", ","], label: "Settings" },
  { keys: ["Enter"], label: "Send; Shift + Enter for a new line" },
];

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="grid gap-1.5 text-sm">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

/** Whether the server's parts are working; checked when the dialog opens and on request. */
function ConnectionStatus() {
  const [checks, setChecks] = useState<StatusCheck[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [round, setRound] = useState(0);

  useEffect(() => {
    let current = true;
    api
      .status()
      .then((result) => current && setChecks(result))
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, [round]);

  function checkAgain() {
    setChecks(null);
    setFailed(false);
    setRound((count) => count + 1);
  }

  return (
    <section className="grid gap-1.5 text-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-muted-foreground">
          Connection status
        </h3>
        <Button
          variant="ghost"
          size="xs"
          onClick={checkAgain}
          disabled={!checks && !failed}
        >
          <RefreshCwIcon />
          Check again
        </Button>
      </div>
      {failed ? (
        <p className="flex items-center gap-2 text-destructive">
          <CircleXIcon className="size-4" /> Can&apos;t reach the Icarus server.
        </p>
      ) : !checks ? (
        <p className="text-muted-foreground">Checking…</p>
      ) : (
        <ul className="grid gap-1.5">
          {checks.map(({ id, label, ok, detail }) => (
            <li key={id} className="flex items-start gap-2">
              {ok ? (
                <CircleCheckIcon className="mt-0.5 size-4 shrink-0 text-emerald-600" />
              ) : (
                <CircleXIcon className="mt-0.5 size-4 shrink-0 text-destructive" />
              )}
              <span>
                <span className="font-medium">{label}</span>
                <span className="text-muted-foreground"> · {detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** What the assistant can do, how to drive the app, and whether its parts are working. */
export function HelpDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85svh] gap-5 overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Get help</DialogTitle>
          <DialogDescription>
            What Icarus can do and how to use it.
          </DialogDescription>
        </DialogHeader>

        <Section title="Try asking">
          <ul className="grid gap-1">
            {EXAMPLES.map((example) => (
              <li key={example} className="rounded-md bg-muted px-2.5 py-1.5">
                {example}
              </li>
            ))}
          </ul>
        </Section>

        <Section title="What it can do">
          <ul className="grid list-disc gap-0.5 pl-5">
            {CAPABILITIES.map((capability) => (
              <li key={capability}>{capability}</li>
            ))}
          </ul>
          <p className="text-muted-foreground">
            CFD runs in the background: follow it from the chart button at the
            top right.
          </p>
        </Section>

        <Section title="Keyboard shortcuts">
          <ul className="grid gap-1">
            {SHORTCUTS.map(({ keys, label }) => (
              <li
                key={label}
                className="flex items-center justify-between gap-3"
              >
                <span>{label}</span>
                <span className="flex gap-1">
                  {keys.map((key) => (
                    <Kbd key={key}>{key}</Kbd>
                  ))}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground">
            On a Mac, use ⌘ in place of Ctrl.
          </p>
        </Section>

        <ConnectionStatus />
      </DialogContent>
    </Dialog>
  );
}
