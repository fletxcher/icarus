"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeftIcon,
  CheckIcon,
  CircleAlertIcon,
  CircleXIcon,
  LoaderCircleIcon,
  Trash2Icon,
} from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatDuration, jobName, STAGE_LABELS } from "@/lib/chat/simulation-format";
import type {
  MonitorData,
  Simulation,
  SimulationInfo,
  SimulationProgress,
} from "@/lib/chat/simulation-types";
import { cn } from "@/lib/utils";
import { useSimulationMonitor, type Monitor } from "@/hooks/use-simulation-monitor";
import { DeleteSimulationDialog } from "./delete-simulation-dialog";
import { PlotBlock, type PlotFigure } from "./plot-block";

const MAX_POINTS = 600;

const TABS = ["Overview", "Residuals", "Setup", "Log", "Results"] as const;
type Tab = (typeof TABS)[number];

/** Keeps at most `max` evenly spaced points of aligned series, always including the last. */
function thin<T>(max: number, ...series: T[][]): T[][] {
  const length = series[0]?.length ?? 0;
  if (length <= max) return series;
  const step = Math.ceil(length / max);
  return series.map((values) =>
    values.filter((_, index) => index % step === 0 || index === length - 1),
  );
}

function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  return Math.abs(value) >= 1e4 || (value !== 0 && Math.abs(value) < 1e-3)
    ? value.toExponential(2)
    : value.toPrecision(digits);
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm font-medium tabular-nums">{value}</div>
    </div>
  );
}

function Table({ rows }: { rows: [string, string][] }) {
  return (
    <table className="w-full text-left text-sm">
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label} className="border-t first:border-t-0">
            <th className="w-1/2 py-1.5 pr-3 font-normal text-muted-foreground">{label}</th>
            <td className="py-1.5 tabular-nums">{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A pill as tall as the icon buttons beside it, so they line up. */
function StatusBadge({ status }: { status: Simulation["status"] | undefined }) {
  const style =
    status === "completed"
      ? "border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-300"
      : status === "failed"
        ? "border-destructive/30 bg-destructive/10 text-destructive"
        : "bg-muted/40 text-muted-foreground";
  return (
    <span className={cn("flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium", style)}>
      {status === "completed" ? (
        <CheckIcon className="size-3.5" />
      ) : status === "failed" ? (
        <CircleXIcon className="size-3.5" />
      ) : (
        <LoaderCircleIcon className="size-3.5 animate-spin" />
      )}
      {status === "completed" ? "Completed" : status === "failed" ? "Failed" : status === "queued" ? "Queued" : "Running"}
    </span>
  );
}

function StageList({ progress, info }: { progress: SimulationProgress | null; info: SimulationInfo | null }) {
  const expected = info?.stages_expected ?? Object.keys(STAGE_LABELS);
  const done = new Set(progress?.stages ?? []);
  const ended = progress?.status === "completed" || progress?.status === "failed";
  return (
    <ol className="flex flex-wrap gap-1.5">
      {expected.map((stage) => {
        const current = progress?.stage === stage && !ended;
        return (
          <li
            key={stage}
            className={cn(
              "rounded-full border px-2 py-0.5 text-xs",
              done.has(stage) && !current && "border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-300",
              current && "border-primary/40 bg-primary/10 font-medium text-foreground",
              !done.has(stage) && "text-muted-foreground",
            )}
          >
            {STAGE_LABELS[stage] ?? stage}
          </li>
        );
      })}
    </ol>
  );
}

function lineFigure(
  x: number[],
  series: { name: string; y: (number | null)[] }[],
  layout: PlotFigure["layout"],
): PlotFigure {
  const columns = thin(MAX_POINTS, x, ...series.map((entry) => entry.y));
  return {
    data: series.map((entry, index) => ({
      type: "scatter",
      mode: "lines",
      name: entry.name,
      x: columns[0],
      y: columns[index + 1],
      connectgaps: false,
    })),
    layout: { showlegend: series.length > 1, ...layout },
  };
}

function Overview({ monitor, data }: { monitor: ReturnType<typeof useSimulationMonitor>; data: MonitorData }) {
  const { progress, info } = monitor;
  const unit = progress?.horizon_unit ?? info?.horizon_unit ?? "";
  const courant = data.courant.findLast((value) => value !== null) ?? null;
  const coefficients = useMemo(
    () =>
      data.coeffTime.length
        ? lineFigure(data.coeffTime, [{ name: "Cl", y: data.cl }, { name: "Cd", y: data.cd }], {
            title: { text: "Force coefficients" },
            xaxis: { title: { text: unit === "iterations" ? "iteration" : "time" } },
          })
        : null,
    [data.coeffTime, data.cl, data.cd, unit],
  );

  return (
    <div className="flex flex-col gap-4">
      <StageList progress={progress} info={info} />
      <div>
        <div className="mb-1 flex justify-between text-xs text-muted-foreground">
          <span>
            {progress?.latest_time !== null && progress?.latest_time !== undefined
              ? `${formatNumber(progress.latest_time, 4)} of ${formatNumber(progress.horizon, 4)} ${unit}`
              : "Preparing the run"}
          </span>
          <span>{progress?.fraction != null ? `${Math.round(progress.fraction * 100)}%` : ""}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-500"
            style={{ width: `${Math.round((progress?.fraction ?? 0) * 100)}%` }}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Elapsed" value={formatDuration(progress?.elapsed_s)} />
        <Stat
          label={info?.regime === "steady" ? "Time left (at most)" : "Time left"}
          value={formatDuration(progress?.eta_s)}
        />
        <Stat label="Steps taken" value={String(data.time.length || "–")} />
        <Stat label="Courant (max)" value={formatNumber(courant, 3)} />
      </div>
      {coefficients ? (
        <PlotBlock figure={coefficients} />
      ) : (
        <p className="text-sm text-muted-foreground">Force coefficients appear once the solver starts.</p>
      )}
    </div>
  );
}

function Residuals({ data, unit }: { data: MonitorData; unit: string }) {
  const figure = useMemo(() => {
    const fields = Object.keys(data.residuals);
    if (!data.time.length || !fields.length) return null;
    return lineFigure(
      data.time,
      fields.map((name) => ({ name, y: data.residuals[name].map((v) => (v && v > 0 ? v : null)) })),
      {
        title: { text: "Initial residuals" },
        xaxis: { title: { text: unit === "iterations" ? "iteration" : "time" } },
        yaxis: { type: "log", exponentformat: "power" },
      },
    );
  }, [data.time, data.residuals, unit]);

  if (!figure) return <p className="text-sm text-muted-foreground">Residuals appear once the solver starts.</p>;
  return <PlotBlock figure={figure} />;
}

function Setup({ info, simulation }: { info: SimulationInfo | null; simulation: Simulation | null }) {
  const testCase = info?.case ?? simulation?.case;
  if (!testCase) return <p className="text-sm text-muted-foreground">Loading…</p>;
  const mesh = info?.mesh ?? {};
  const rows: [string, string][] = [
    ["Shape", testCase.shape === "airfoil" && testCase.naca ? `NACA ${testCase.naca}` : testCase.shape],
    ["Reynolds number", testCase.reynolds.toExponential(2)],
    ["Angle of attack", `${testCase.alpha}°`],
    ["Wall", testCase.wall_type.replace("_", "-")],
    ...(testCase.roughness_height ? ([["Roughness", String(testCase.roughness_height)]] as [string, string][]) : []),
    ["Mesh level", testCase.mesh],
  ];
  const solverRows: [string, string][] = info
    ? [
        ["Solver", `${info.solver} (${info.regime})`],
        ["Turbulence", info.turbulence],
        ["Processors", info.processors ? String(info.processors) : "–"],
        ["Run until", `${formatNumber(info.horizon, 4)} ${info.horizon_unit}`],
      ]
    : [];
  const meshRows: [string, string][] = mesh.cells
    ? [
        ["Cells", String(mesh.cells)],
        ["Max non-orthogonality", formatNumber(mesh.max_non_orthogonality, 3)],
        ["Max skewness", formatNumber(mesh.max_skewness, 3)],
        ["Max aspect ratio", formatNumber(mesh.max_aspect_ratio, 3)],
        ["checkMesh", mesh.ok ? "OK" : "reported problems"],
      ]
    : [];

  return (
    <div className="flex flex-col gap-5">
      <section>
        <h3 className="mb-1 text-xs font-medium text-muted-foreground">Case</h3>
        <Table rows={rows} />
      </section>
      {solverRows.length > 0 && (
        <section>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">Solver</h3>
          <Table rows={solverRows} />
        </section>
      )}
      {meshRows.length > 0 && (
        <section>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">Mesh</h3>
          <Table rows={meshRows} />
        </section>
      )}
      {info && Object.keys(info.dictionaries ?? {}).length > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">OpenFOAM settings</h3>
          {Object.entries(info.dictionaries ?? {}).map(([name, text]) => (
            <details key={name} className="rounded-md border">
              <summary className="cursor-pointer px-3 py-1.5 text-sm">{name}</summary>
              <pre className="max-h-72 overflow-auto border-t bg-muted/40 p-3 font-mono text-xs">{text}</pre>
            </details>
          ))}
        </section>
      )}
    </div>
  );
}

function LogTail({ progress }: { progress: SimulationProgress | null }) {
  const ref = useRef<HTMLPreElement>(null);
  const lines = progress?.tail ?? [];
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight });
  }, [lines.length, progress?.stage]);
  return (
    <div>
      <p className="mb-1 text-xs text-muted-foreground">
        {progress?.stage ? `log.${progress.stage}, last lines` : "No log yet"}
      </p>
      <pre ref={ref} className="h-96 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap">
        {lines.join("\n")}
      </pre>
    </div>
  );
}

function Results({ simulation }: { simulation: Simulation | null }) {
  if (!simulation) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (simulation.status === "failed")
    return (
      <p className="rounded-md bg-destructive/10 p-3 text-sm whitespace-pre-wrap text-destructive">
        {simulation.error ?? "The job failed."}
      </p>
    );
  const result = simulation.result;
  if (!result) return <p className="text-sm text-muted-foreground">Results appear when the job finishes.</p>;
  const rows: [string, string][] = [
    ["Cl", formatNumber(result.cl)],
    ["Cd", formatNumber(result.cd)],
    ["Cm", formatNumber(result.cm)],
    ["Cl / Cd", formatNumber(result.cl_over_cd)],
    ...(result.strouhal !== undefined ? ([["Strouhal number", formatNumber(result.strouhal)]] as [string, string][]) : []),
    ...(result.cl_rms !== undefined ? ([["Cl rms", formatNumber(result.cl_rms)]] as [string, string][]) : []),
    ...(result.converged !== undefined
      ? ([["Converged", result.converged ? `yes, after ${result.iterations} iterations` : "no"]] as [string, string][])
      : []),
    ...(result.y_plus_body ? ([["y+ (average / max)", `${formatNumber(result.y_plus_body.average, 3)} / ${formatNumber(result.y_plus_body.max, 3)}`]] as [string, string][]) : []),
    ...(result.averaged_over ? ([["Averaged over", result.averaged_over]] as [string, string][]) : []),
  ];
  const warnings = result.warnings ?? [];
  const images = result.media?.filter((item) => item.kind === "image") ?? [];
  const videos = result.media?.filter((item) => item.kind === "video") ?? [];
  return (
    <div className="flex flex-col gap-5">
      {warnings.length > 0 && (
        <ul className="flex flex-col gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          {warnings.map((warning) => (
            <li key={warning} className="flex gap-2">
              <CircleAlertIcon className="mt-0.5 size-4 shrink-0 text-amber-600" />
              {warning}
            </li>
          ))}
        </ul>
      )}
      <section>
        <Table rows={rows} />
        {result.coefficient_basis && (
          <p className="mt-2 text-xs text-muted-foreground">Coefficients are {result.coefficient_basis}.</p>
        )}
      </section>
      {images.length > 0 && (
        <section className="grid gap-4 lg:grid-cols-2">
          {images.map((item) => (
            <figure key={item.file}>
              {/* eslint-disable-next-line @next/next/no-img-element -- served by the API, not a static asset */}
              <img
                className="w-full rounded-md border"
                alt={item.title}
                loading="lazy"
                src={api.simulationFileUrl(simulation.id, item.file)}
              />
              <figcaption className="mt-1 text-xs text-muted-foreground">{item.title}</figcaption>
            </figure>
          ))}
        </section>
      )}
      {videos.map((item) => (
        <figure key={item.file}>
          <video
            className="w-full rounded-md border"
            controls
            loop
            preload="metadata"
            src={api.simulationFileUrl(simulation.id, item.file)}
          />
          <figcaption className="mt-1 text-xs text-muted-foreground">
            {item.title}
            {item.frames ? ` · ${item.frames} frames` : ""}
            {item.duration_s ? `, ${item.duration_s} s` : ""}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

/** The delete button; leaves the page once the job is gone. */
function DeleteSimulationButton({ jobId, simulation }: { jobId: string; simulation: Simulation | null }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  return (
    <>
      <Button variant="ghost" size="icon-sm" aria-label="Delete simulation" onClick={() => setConfirming(true)}>
        <Trash2Icon />
      </Button>
      <DeleteSimulationDialog
        open={confirming}
        onOpenChange={setConfirming}
        jobId={jobId}
        simulation={simulation}
        onDeleted={() => router.push("/")}
      />
    </>
  );
}

/** One CFD job in full: progress, residuals, force coefficients, setup, log and results. */
export function SimulationDetail({ jobId, monitor }: { jobId: string; monitor: Monitor }) {
  const [tab, setTab] = useState<Tab>("Overview");
  const { simulation, info, progress, data, error, missing } = monitor;
  const unit = progress?.horizon_unit ?? info?.horizon_unit ?? "";

  if (missing) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm text-muted-foreground">This simulation doesn&apos;t exist, or was deleted.</p>
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
              {simulation ? jobName(simulation.case) : "CFD simulation"}
            </h2>
            <div className="truncate font-mono text-xs text-muted-foreground">{jobId}</div>
          </div>
          {/* One group, centred on the title block, so the badge and the icon share a midline. */}
          <div className="flex items-center gap-1.5">
            <StatusBadge status={simulation?.status ?? progress?.status} />
            <DeleteSimulationButton jobId={jobId} simulation={simulation} />
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
        {tab === "Overview" && <Overview monitor={monitor} data={data} />}
        {tab === "Residuals" && <Residuals data={data} unit={unit} />}
        {tab === "Setup" && <Setup info={info} simulation={simulation} />}
        {tab === "Log" && <LogTail progress={progress} />}
        {tab === "Results" && <Results simulation={simulation} />}
      </div>
    </div>
  );
}

/** The page at /simulations/<id>: the app frame around a job's full monitor. */
export function SimulationPage({ jobId }: { jobId: string }) {
  const monitor = useSimulationMonitor(jobId);
  const title = monitor.simulation ? jobName(monitor.simulation.case) : "Simulation";
  return (
    <AppShell title={title} simulationId={jobId}>
      <SimulationDetail jobId={jobId} monitor={monitor} />
    </AppShell>
  );
}
