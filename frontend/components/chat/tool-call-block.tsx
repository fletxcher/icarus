"use client";

import Link from "next/link";
import {
  ActivityIcon,
  ChartLineIcon,
  ChevronRightIcon,
  CircleCheckIcon,
  CircleXIcon,
  CpuIcon,
  LoaderCircleIcon,
  PlaneIcon,
  SearchIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ToolPart } from "@/lib/chat/types";
import { cn } from "@/lib/utils";
import { Markdown } from "./markdown";

// Display names for the backend's tools (backend/src/api/agent/tools.py).
const TOOLS: Record<string, { label: string; icon: LucideIcon }> = {
  run_xfoil_polar: { label: "XFOIL analysis", icon: PlaneIcon },
  search_naca4_airfoils: { label: "Airfoil search", icon: SearchIcon },
  submit_cfd_simulation: { label: "CFD simulation", icon: CpuIcon },
  submit_fea_simulation: { label: "FEA simulation", icon: CpuIcon },
  get_simulation_status: { label: "Simulation status", icon: CpuIcon },
  create_plot: { label: "Plot", icon: ChartLineIcon },
};

const STATUS = {
  running: { label: "Running", icon: LoaderCircleIcon, className: "animate-spin text-muted-foreground" },
  success: { label: "Done", icon: CircleCheckIcon, className: "text-emerald-600 dark:text-emerald-400" },
  error: { label: "Failed", icon: CircleXIcon, className: "text-destructive" },
};

/** One-line summary of the most telling arguments, e.g. "NACA 2412 · Re 1e+6". */
function summarizeArgs(args: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof args.naca === "string") parts.push(`NACA ${args.naca}`);
  if (typeof args.reynolds === "number") parts.push(`Re ${args.reynolds.toExponential(1)}`);
  if (typeof args.alpha === "number") parts.push(`α ${args.alpha}°`);
  if (typeof args.job_id === "string") parts.push(args.job_id.slice(0, 8));
  if (typeof args.material === "string") parts.push(args.material);
  if (typeof args.spin_rpm === "number" && args.spin_rpm > 0) parts.push(`${args.spin_rpm} rpm`);
  if (typeof args.gas_temperature_c === "number") parts.push(`${args.gas_temperature_c} °C`);
  return parts.join(" · ");
}

const JOB_ID = /Job ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i;

/** The CFD job a tool call started or asked about, once its id is known. */
function jobIdOf(part: ToolPart): string | null {
  if (part.name === "get_simulation_status" && typeof part.args.job_id === "string") {
    return part.args.job_id;
  }
  if ((part.name === "submit_cfd_simulation" || part.name === "submit_fea_simulation") && part.result) {
    return JOB_ID.exec(part.result)?.[1] ?? null;
  }
  return null;
}

export function ToolCallBlock({ part }: { part: ToolPart }) {
  const jobId = jobIdOf(part);

  return (
    <div className="flex flex-col gap-1.5">
      <ToolCallCard part={part} />
      {jobId && (
        <Button variant="outline" size="sm" className="self-start" asChild>
          <Link href={`/simulations/${jobId}`}>
            <ActivityIcon />
            Open simulation
          </Link>
        </Button>
      )}
    </div>
  );
}

function ToolCallCard({ part }: { part: ToolPart }) {
  const tool = TOOLS[part.name] ?? { label: part.name, icon: WrenchIcon };
  const status = STATUS[part.status];
  const summary = summarizeArgs(part.args);

  return (
    <Collapsible className="rounded-lg border bg-muted/30">
      <CollapsibleTrigger className="group flex w-full items-center gap-2 px-3 py-2 text-left text-sm">
        <tool.icon className="size-4 shrink-0 text-muted-foreground" />
        <span className="font-medium">{tool.label}</span>
        {summary && <span className="truncate text-muted-foreground">{summary}</span>}
        <span className="ml-auto flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
          <status.icon className={cn("size-4", status.className)} />
          {status.label}
          <ChevronRightIcon className="size-3.5 transition-transform group-data-[state=open]:rotate-90" />
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="flex flex-col gap-3 border-t px-3 py-3">
          <section>
            <h4 className="mb-1 text-xs font-medium text-muted-foreground">Arguments</h4>
            <pre className="overflow-x-auto rounded-md bg-muted/60 p-2 font-mono text-xs">
              {JSON.stringify(part.args, null, 2)}
            </pre>
          </section>
          <section>
            <h4 className="mb-1 text-xs font-medium text-muted-foreground">Result</h4>
            {part.result === null ? (
              <p className="text-xs text-muted-foreground">Waiting for the tool to finish…</p>
            ) : part.status === "error" ? (
              <pre className="overflow-x-auto rounded-md bg-destructive/10 p-2 font-mono text-xs whitespace-pre-wrap text-destructive">
                {part.result}
              </pre>
            ) : (
              <div className="text-sm">
                <Markdown content={part.result} />
              </div>
            )}
          </section>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
