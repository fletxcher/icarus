"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { formatDuration, shapeName } from "@/lib/chat/simulation-format";
import type { SimulationUsage, UsagePeriod } from "@/lib/chat/simulation-types";

const PERIODS: { key: keyof SimulationUsage; label: string }[] = [
  { key: "week", label: "Last 7 days" },
  { key: "month", label: "Last 30 days" },
  { key: "all_time", label: "All time" },
];

function Period({ label, usage }: { label: string; usage: UsagePeriod }) {
  const shapes = Object.entries(usage.by_shape).sort((a, b) => b[1] - a[1]);
  return (
    <section className="grid gap-1.5 rounded-lg border p-3 text-sm">
      <div className="flex items-baseline justify-between">
        <h3 className="font-medium">{label}</h3>
        <span className="text-muted-foreground">
          {usage.runs} {usage.runs === 1 ? "run" : "runs"}
        </span>
      </div>
      {usage.runs === 0 ? (
        <p className="text-muted-foreground">No simulations yet.</p>
      ) : (
        <>
          <p className="text-muted-foreground">
            {usage.completed} completed · {usage.failed} failed
            {usage.running > 0 && ` · ${usage.running} running`}
          </p>
          <p className="text-muted-foreground">
            About {formatDuration(usage.compute_s)} on the work station
          </p>
          <p className="text-muted-foreground">
            {shapes
              .map(([shape, count]) => `${shapeName(shape)} ${count}`)
              .join(" · ")}
          </p>
        </>
      )}
    </section>
  );
}

/** CFD simulations started this week, this month and in all; mount it to load fresh numbers. */
export function UsageBody() {
  const [usage, setUsage] = useState<SimulationUsage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let current = true;
    api
      .simulationUsage()
      .then((result) => current && setUsage(result))
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, []);

  if (failed)
    return (
      <p className="text-sm text-destructive">
        Couldn&apos;t load usage. Try again.
      </p>
    );
  return (
    <div className="grid gap-2">
      {PERIODS.map(({ key, label }) =>
        usage ? (
          <Period key={key} label={label} usage={usage[key]} />
        ) : (
          <Skeleton key={key} className="h-24 rounded-lg" />
        ),
      )}
    </div>
  );
}
