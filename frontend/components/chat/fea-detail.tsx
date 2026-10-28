"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeftIcon, CircleAlertIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Monitor } from "@/hooks/use-simulation-monitor";
import { api } from "@/lib/api";
import { MATERIAL_NAMES, jobName } from "@/lib/chat/simulation-format";
import type { FeaCase, FeaResult, Simulation } from "@/lib/chat/simulation-types";
import { cn } from "@/lib/utils";
import { ModelViewer } from "./model-viewer";
import { ListToggle, StatusBadge, Table } from "./simulation-detail";

const TABS = ["Overview", "Setup", "Log"] as const;
type Tab = (typeof TABS)[number];

const STAGE_NAMES: Record<string, string> = {
  starting: "Starting",
  mesh: "Meshing the solid",
  deck: "Writing the solver input",
  solve: "Solving",
  modal: "Natural frequencies",
  results: "Reading the results",
};

const mpa = (value: number) => `${value.toFixed(value >= 100 ? 0 : 1)} MPa`;
const point = (xyz: number[]) => `(${xyz.map((v) => v.toFixed(1)).join(", ")}) mm`;

function resultRows(result: FeaResult): [string, string][] {
  const [low, high] = result.bounding_box_mm;
  const rows: [string, string][] = [
    ["Material", result.material],
    [
      "Mesh",
      `${result.elements.toLocaleString()} tetrahedra, ${result.nodes.toLocaleString()} nodes`,
    ],
    ["Size", `${high.map((h, i) => (h - low[i]).toFixed(1)).join(" × ")} mm`],
    ["Mass", `${(result.mass_kg * 1000).toFixed(1)} g`],
    [
      "Max displacement",
      `${result.max_displacement_mm.toPrecision(3)} mm at ${point(result.max_displacement_at_mm)}`,
    ],
    ["Typical von Mises (95th percentile)", mpa(result.typical_von_mises_mpa)],
    [
      "Peak von Mises away from the support",
      `${mpa(result.max_von_mises_away_from_support_mpa)} at ${point(result.max_von_mises_away_at_mm)}`,
    ],
    [
      "Peak von Mises anywhere",
      `${mpa(result.max_von_mises_mpa)} at ${point(result.max_von_mises_at_mm)}`,
    ],
    [
      "Safety factor on yield, away from the support",
      result.min_safety_factor_yield_away_from_support.toFixed(2),
    ],
    ["Safety factor on yield, anywhere", result.min_safety_factor_yield_anywhere.toFixed(2)],
  ];
  if (result.temperature_c)
    rows.push([
      "Temperature",
      `${result.temperature_c[0].toFixed(0)} to ${result.temperature_c[1].toFixed(0)} °C`,
    ]);
  if (result.natural_frequencies_hz)
    rows.push([
      "Natural frequencies",
      result.natural_frequencies_hz.map((f) => `${f.toFixed(0)} Hz`).join(", "),
    ]);
  if (result.engine_order_hz)
    rows.push([
      "Engine orders",
      Object.entries(result.engine_order_hz)
        .map(([order, hz]) => `${order} ${hz.toFixed(0)} Hz`)
        .join(", "),
    ]);
  return rows;
}

function caseRows(testCase: FeaCase): [string, string][] {
  const rows: [string, string][] = [
    ["Solid", testCase.geometry_name ?? "uploaded STL"],
    ["Units", testCase.units],
    ["Material", MATERIAL_NAMES[testCase.material] ?? testCase.material],
    ["Held fixed", `the ${testCase.support_side} end along ${testCase.support_axis}`],
    ["Mesh level", testCase.mesh],
  ];
  if (testCase.spin_rpm) {
    rows.push([
      "Spin",
      `${testCase.spin_rpm} rpm about the axis through (${testCase.spin_axis_origin.join(", ")}) along (${testCase.spin_axis_direction.join(", ")})`,
    ]);
  }
  if (testCase.pressure_mpa)
    rows.push(["Pressure", `${testCase.pressure_mpa} MPa on the outer surface`]);
  if (testCase.gas_temperature_c !== null) {
    rows.push(["Gas", `${testCase.gas_temperature_c} °C, ${testCase.htc_w_m2k} W/m²K`]);
    rows.push(["Support temperature", `${testCase.base_temperature_c} °C`]);
  }
  rows.push(["Natural frequencies", testCase.modal ? "yes" : "no"]);
  return rows;
}

function Overview({ simulation, monitor }: { simulation: Simulation | null; monitor: Monitor }) {
  const { fea } = monitor;
  if (!simulation) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (simulation.status === "failed")
    return (
      <p className="rounded-md bg-destructive/10 p-3 text-sm whitespace-pre-wrap text-destructive">
        {simulation.error ?? "The job failed."}
      </p>
    );
  const result = simulation.result as FeaResult | null;
  if (!result) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm">{fea ? (STAGE_NAMES[fea.stage] ?? fea.stage) : "Starting"}…</p>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.max((fea?.fraction ?? 0) * 100, 4)}%` }}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          The solid is meshed with tetrahedra and then solved with CalculiX; this takes a few
          minutes.
        </p>
        {simulation.case.kind === "fea" && (
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
      <p className="text-xs text-muted-foreground">{result.peak_stress_note}</p>
    </div>
  );
}

/** One FEA job in full: progress and, when it's done, stresses, deflection and frequencies. */
export function FeaDetail({ jobId, monitor }: { jobId: string; monitor: Monitor }) {
  const [tab, setTab] = useState<Tab>("Overview");
  const { simulation, fea, error, missing } = monitor;
  const testCase = simulation?.case.kind === "fea" ? simulation.case : null;

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
              {simulation ? jobName(simulation.case) : "FEA simulation"}
            </h2>
            <div className="truncate font-mono text-xs text-muted-foreground">{jobId}</div>
          </div>
          <div className="flex items-center gap-1.5">
            <StatusBadge status={simulation?.status ?? fea?.status} />
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
            {fea?.lines.length ? fea.lines.join("\n") : "No log yet"}
          </pre>
        )}
      </div>
    </div>
  );
}
