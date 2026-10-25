"use client";

import Link from "next/link";
import { CheckIcon, ChevronRightIcon, CircleXIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatDuration, jobName, STAGE_LABELS } from "@/lib/chat/simulation-format";
import type { SimulationSummary } from "@/lib/chat/simulation-types";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";

function ProgressBar({ fraction }: { fraction: number | null }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
      <div
        className={cn(
          "h-full rounded-full bg-primary transition-[width] duration-500",
          fraction === null && "w-1/3 animate-pulse",
        )}
        style={fraction === null ? undefined : { width: `${Math.round(fraction * 100)}%` }}
      />
    </div>
  );
}

function JobRow({ job, current }: { job: SimulationSummary; current: boolean }) {
  const running = job.status === "running" || job.status === "queued";
  const detail = running
    ? [
        job.stage ? (STAGE_LABELS[job.stage] ?? job.stage) : "Starting",
        job.eta_s != null ? `${formatDuration(job.eta_s)} left` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : job.status === "failed"
      ? "Failed"
      : "Completed";

  return (
    <li>
      <Link
        href={`/simulations/${job.id}`}
        aria-current={current ? "page" : undefined}
        className={cn(
          "group flex w-full flex-col gap-2 rounded-lg border bg-muted/30 px-3 py-2.5 text-left transition-colors hover:bg-muted/60",
          current && "border-primary/40 bg-muted/70",
        )}
      >
        <div className="flex items-center gap-2">
          {!running &&
            (job.status === "failed" ? (
              <CircleXIcon className="size-4 shrink-0 text-destructive" />
            ) : (
              <CheckIcon className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
            ))}
          <span className="min-w-0 flex-1 truncate text-sm font-medium">{jobName(job.case)}</span>
          {running && job.fraction !== null && (
            <span className="text-xs tabular-nums text-muted-foreground">{Math.round(job.fraction * 100)}%</span>
          )}
          <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </div>
        {running && <ProgressBar fraction={job.fraction} />}
        <div className="truncate text-xs text-muted-foreground">{detail}</div>
      </Link>
    </li>
  );
}

function SimulationList({ currentId }: { currentId?: string }) {
  const simulations = useChatStore((state) => state.simulations);
  const closeSimulations = useChatStore((state) => state.closeSimulations);
  const running = simulations.filter((job) => job.status === "running" || job.status === "queued");
  const finished = simulations.filter((job) => job.status === "completed" || job.status === "failed");

  return (
    <>
      <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
        <h2 className="flex-1 text-sm font-medium">Simulations</h2>
        <Button variant="ghost" size="icon-sm" aria-label="Close simulations" onClick={closeSimulations}>
          <XIcon />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {running.length === 0 ? (
          <p className="px-1 py-2 text-xs text-muted-foreground">No simulations running</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {running.map((job) => (
              <JobRow key={job.id} job={job} current={job.id === currentId} />
            ))}
          </ul>
        )}
        {finished.length > 0 && (
          <section className="mt-5">
            <h3 className="mb-2 px-1 text-xs font-medium text-muted-foreground">Recent</h3>
            <ul className="flex flex-col gap-2">
              {finished.map((job) => (
                <JobRow key={job.id} job={job} current={job.id === currentId} />
              ))}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}

/** The right-hand panel listing the simulations; each one opens on its own page. */
export function SimulationsPanel({ currentId }: { currentId?: string }) {
  return (
    <aside className="flex min-h-0 w-full flex-col border-l bg-background md:w-80 md:shrink-0">
      <SimulationList currentId={currentId} />
    </aside>
  );
}
