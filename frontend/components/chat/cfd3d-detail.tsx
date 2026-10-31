"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeftIcon, CircleAlertIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Monitor } from "@/hooks/use-simulation-monitor";
import { api } from "@/lib/api";
import { DOMAIN_NAMES, jobName } from "@/lib/chat/simulation-format";
import type { Cfd3dCase, Cfd3dResult, Simulation } from "@/lib/chat/simulation-types";
import { cn } from "@/lib/utils";
import { ModelViewer } from "./model-viewer";
import { ListToggle, StatusBadge, Table } from "./simulation-detail";

const TABS = ["Overview", "Setup", "Log"] as const;
type Tab = (typeof TABS)[number];

const STAGE_NAMES: Record<string, string> = {
  starting: "Starting",
  mesh: "Meshing the flow around the solid",
  solve: "Solving",
  results: "Reading the results",
};

const newtons = (value: number) => `${value.toPrecision(3)} N`;
const plain = (value: number) => value.toPrecision(4);

function resultRows(result: Cfd3dResult): [string, string][] {
  const rows: [string, string][] = [
    ["Domain", DOMAIN_NAMES[result.domain] ?? result.domain],
    ["Mesh", `${result.cells.toLocaleString()} cells (${result.mesh_level})`],
    [
      "Iterations",
      `${result.iterations}${result.converged ? ", converged" : ", not converged"}`,
    ],
    ["Dynamic pressure", `${result.dynamic_pressure_pa.toPrecision(4)} Pa`],
    ["Force on the solid", result.force_n.map((v) => v.toPrecision(3)).join(", ") + " N"],
    ["Drag (along the flow)", newtons(result.drag_n)],
    ["Cross force", newtons(result.cross_force_n)],
  ];
  if (result.drag_coefficient !== null)
    rows.push(["Drag coefficient", `${plain(result.drag_coefficient)} on the frontal area`]);
  if (result.cross_force_coefficient !== null)
    rows.push(["Cross-force coefficient", plain(result.cross_force_coefficient)]);
  if (result.y_plus)
    rows.push(["Wall y+ (average)", `${result.y_plus.average.toFixed(0)} (max ${result.y_plus.max.toFixed(0)})`]);
  if (result.total_pressure_loss_coefficient !== undefined) {
    rows.push(["Total-pressure loss coefficient", plain(result.total_pressure_loss_coefficient)]);
    rows.push(["Flow turning", `${result.flow_turning_deg?.toFixed(1)}°`]);
    rows.push([
      "Inlet → exit flow angle",
      `${result.inlet_flow_angle_deg?.toFixed(1)}° → ${result.exit_flow_angle_deg?.toFixed(1)}°`,
    ]);
    rows.push(["Mass balance error", `${((result.mass_balance_error ?? 0) * 100).toFixed(2)}%`]);
    if (result.blade_force_per_span_n_per_m !== undefined)
      rows.push(["Blade force per metre of span", `${result.blade_force_per_span_n_per_m} N/m`]);
  }
  if (result.thrust_n !== undefined) {
    rows.push(["Thrust", newtons(result.thrust_n)]);
    rows.push(["Torque", `${result.torque_nm?.toPrecision(3)} N·m`]);
    rows.push(["Shaft power", `${result.shaft_power_w?.toPrecision(3)} W`]);
    if (result.thrust_coefficient !== undefined)
      rows.push(["Thrust / power coefficient", `${result.thrust_coefficient} / ${result.power_coefficient}`]);
    if (result.advance_ratio !== undefined) rows.push(["Advance ratio", String(result.advance_ratio)]);
    if (result.propulsive_efficiency !== undefined)
      rows.push(["Propulsive efficiency", `${(result.propulsive_efficiency * 100).toFixed(1)}%`]);
  }
  return rows;
}

function caseRows(testCase: Cfd3dCase): [string, string][] {
  const rows: [string, string][] = [
    ["Solid", testCase.geometry_name ?? "uploaded STL"],
    ["Units", testCase.units],
    ["Domain", DOMAIN_NAMES[testCase.domain] ?? testCase.domain],
    ["Speed", `${testCase.speed_ms} m/s along (${testCase.flow_direction.join(", ")})`],
    ["Air", `${testCase.density_kg_m3} kg/m³, ν ${testCase.kinematic_viscosity_m2_s} m²/s`],
    ["Turbulence intensity", `${(testCase.turbulence_intensity * 100).toFixed(1)}%`],
    ["Mesh level", testCase.mesh],
    ["Iterations", testCase.iterations ? String(testCase.iterations) : "set by the mesh level"],
    ["Cavities filled", testCase.fill_cavities ? "yes" : "no"],
  ];
  if (testCase.domain === "passage")
    rows.push(["Pitch", `${testCase.pitch} m along ${testCase.pitch_axis}`]);
  if (testCase.domain === "rotating")
    rows.push([
      "Spin",
      `${testCase.spin_rpm} rpm about the axis through (${testCase.spin_axis_origin.join(", ")}) along (${testCase.spin_axis_direction.join(", ")})`,
    ]);
  return rows;
}

function Overview({ simulation, monitor }: { simulation: Simulation | null; monitor: Monitor }) {
  const { fea: progress } = monitor;
  if (!simulation) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (simulation.status === "failed")
    return (
      <p className="rounded-md bg-destructive/10 p-3 text-sm whitespace-pre-wrap text-destructive">
        {simulation.error ?? "The job failed."}
      </p>
    );
  const result = simulation.result as Cfd3dResult | null;
  if (!result) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm">
          {progress ? (STAGE_NAMES[progress.stage] ?? progress.stage) : "Starting"}…
        </p>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.max((progress?.fraction ?? 0) * 100, 4)}%` }}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          The flow around the solid is meshed with snappyHexMesh and solved with OpenFOAM; this
          takes from a few minutes to over an hour, depending on the mesh level.
        </p>
        {simulation.case.kind === "cfd3d" && (
          <ModelViewer
            source={{ type: "stl", url: api.fileOriginalUrl(simulation.case.geometry_file_id) }}
          />
        )}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {result.warnings && result.warnings.length > 0 && (
        <ul className="flex flex-col gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          {result.warnings.map((warning) => (
            <li key={warning} className="flex gap-2">
              <CircleAlertIcon className="mt-0.5 size-4 shrink-0 text-amber-600" />
              {warning}
            </li>
          ))}
        </ul>
      )}
      <ModelViewer
        source={{ type: "fea", url: api.simulationFileUrl(simulation.id, "view.json") }}
      />
      <Table rows={resultRows(result)} />
    </div>
  );
}

/** One 3D CFD job in full: progress and, when it's done, forces and the pressure on the solid. */
export function Cfd3dDetail({ jobId, monitor }: { jobId: string; monitor: Monitor }) {
  const [tab, setTab] = useState<Tab>("Overview");
  const { simulation, fea: progress, error, missing } = monitor;
  const testCase = simulation?.case.kind === "cfd3d" ? simulation.case : null;

  if (missing) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm text-muted-foreground">
          This simulation doesn&apos;t exist, or was deleted.
        </p>
        <Button variant="outline" size="sm" asChild>
          <Link href="/">Back to chat</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 p-4 md:p-6">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon-sm" aria-label="Back to chat" asChild>
            <Link href="/">
              <ArrowLeftIcon />
            </Link>
          </Button>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-lg font-semibold tracking-tight">
              {simulation ? jobName(simulation.case) : "3D CFD simulation"}
            </h2>
            <div className="truncate font-mono text-xs text-muted-foreground">{jobId}</div>
          </div>
          <div className="flex items-center gap-1.5">
            <StatusBadge status={simulation?.status ?? progress?.status} />
            <ListToggle jobId={jobId} simulation={simulation} />
          </div>
        </div>
        <nav className="flex gap-1 border-b pb-1.5" role="tablist">
          {TABS.map((name) => (
            <button
              key={name}
              role="tab"
              aria-selected={tab === name}
              onClick={() => setTab(name)}
              className={cn(
                "rounded-md px-2.5 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground",
                tab === name && "bg-muted font-medium text-foreground",
              )}
            >
              {name}
            </button>
          ))}
        </nav>
        {error && (
          <p className="rounded-md bg-destructive/10 p-2 text-xs text-destructive">
            Couldn&apos;t load the live details: {error}. Retrying…
          </p>
        )}
        {tab === "Overview" && <Overview simulation={simulation} monitor={monitor} />}
        {tab === "Setup" &&
          (testCase ? (
            <Table rows={caseRows(testCase)} />
          ) : (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ))}
        {tab === "Log" && (
          <pre className="h-96 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap">
            {progress?.lines.length ? progress.lines.join("\n") : "No log yet"}
          </pre>
        )}
      </div>
    </div>
  );
}
