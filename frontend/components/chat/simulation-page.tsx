"use client";

import { AppShell } from "@/components/app-shell";
import { useSimulationMonitor } from "@/hooks/use-simulation-monitor";
import { jobName } from "@/lib/chat/simulation-format";
import { FeaDetail } from "./fea-detail";
import { SimulationDetail } from "./simulation-detail";

/** The page at /simulations/<id>: the app frame around a job's full monitor (CFD or FEA). */
export function SimulationPage({ jobId }: { jobId: string }) {
  const monitor = useSimulationMonitor(jobId);
  const title = monitor.simulation ? jobName(monitor.simulation.case) : "Simulation";
  return (
    <AppShell title={title} simulationId={jobId}>
      {monitor.simulation?.case.kind === "fea" ? (
        <FeaDetail jobId={jobId} monitor={monitor} />
      ) : (
        <SimulationDetail jobId={jobId} monitor={monitor} />
      )}
    </AppShell>
  );
}
