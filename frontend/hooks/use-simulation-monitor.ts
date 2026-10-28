"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type {
  FeaProgress,
  MonitorData,
  Simulation,
  SimulationInfo,
  SimulationProgress,
} from "@/lib/chat/simulation-types";

const POLL_MS = 2000;
const RETRY_MS = 5000;

export type Monitor = {
  simulation: Simulation | null;
  info: SimulationInfo | null;
  /** The latest progress call, minus the series (those accumulate in `data`). */
  progress: SimulationProgress | null;
  /** An FEA job's progress (a CFD job's is in `progress` and `data`). */
  fea: FeaProgress | null;
  data: MonitorData;
  error: string | null;
  /** The job doesn't exist (any more), e.g. it was deleted. */
  missing: boolean;
};

const EMPTY: MonitorData = {
  time: [],
  courant: [],
  residuals: {},
  coeffTime: [],
  cl: [],
  cd: [],
  cm: [],
};

/** Appends the new steps and coefficient rows of a progress call to what's been collected. */
function accumulate(data: MonitorData, progress: SimulationProgress): MonitorData {
  const { steps, coefficients } = progress;
  const before = data.time.length;
  const residuals = { ...data.residuals };
  for (const [name, values] of Object.entries(steps.residuals)) {
    // A field that first shows up later (k, omega after the first steps) is padded back.
    const existing = residuals[name] ?? new Array<number | null>(before).fill(null);
    residuals[name] = existing.concat(values);
  }
  for (const name of Object.keys(residuals)) {
    if (!(name in steps.residuals) && steps.time.length) {
      residuals[name] = residuals[name].concat(
        new Array<number | null>(steps.time.length).fill(null),
      );
    }
  }
  return {
    time: data.time.concat(steps.time),
    courant: data.courant.concat(steps.courant_max),
    residuals,
    coeffTime: data.coeffTime.concat(coefficients.time),
    cl: data.cl.concat(coefficients.cl),
    cd: data.cd.concat(coefficients.cd),
    cm: data.cm.concat(coefficients.cm),
  };
}

/**
 * Follows a job: polls the backend for what the run did since the last call and keeps
 * the series. It catches up on a long log at once, polls every couple of seconds while the
 * job runs, and stops when the job has ended and everything has been read.
 */
export function useSimulationMonitor(jobId: string): Monitor {
  const [state, setState] = useState<Monitor>({
    simulation: null,
    info: null,
    progress: null,
    fea: null,
    data: EMPTY,
    error: null,
    missing: false,
  });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let logOffset = 0;
    let coeffOffset = 0;
    let stageKey = "";
    let data = EMPTY;
    setState({
      simulation: null,
      info: null,
      progress: null,
      fea: null,
      data: EMPTY,
      error: null,
      missing: false,
    });

    async function tick() {
      let delay = POLL_MS;
      try {
        // The job's record comes first and comes from the database alone, so the page knows
        // the job (and can delete it) even when the work station can't be reached.
        const simulation = await api.getSimulation(jobId);
        if (cancelled) return;
        setState((previous) => ({ ...previous, simulation, missing: false }));

        if (simulation.case.kind === "fea") {
          // An FEA job has no residual or coefficient series: just its stage and log.
          const fea = await api.feaProgress(jobId);
          if (cancelled) return;
          setState((previous) => ({ ...previous, fea, error: null }));
          if (fea.status === "completed" || fea.status === "failed") return;
          timer = setTimeout(tick, POLL_MS);
          return;
        }

        const progress = await api.simulationProgress(jobId, logOffset, coeffOffset);
        if (cancelled) return;
        logOffset = progress.log_offset;
        coeffOffset = progress.coeff_offset;
        const received = progress.steps.time.length + progress.coefficients.time.length;
        data = accumulate(data, progress);

        // The setup facts (mesh quality, dictionaries) appear as the run moves on.
        const key = progress.stages.join(",");
        const info =
          key !== stageKey ? await api.simulationInfo(jobId).catch(() => null) : undefined;
        if (cancelled) return;
        if (info !== undefined) stageKey = key;
        setState((previous) => ({
          ...previous,
          info: info ?? previous.info,
          progress,
          data,
          error: null,
        }));

        const ended = progress.status === "completed" || progress.status === "failed";
        if (ended && received === 0) return; // everything has been read
        if (received > 0) delay = ended ? 0 : 250; // still catching up
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 404) {
          setState((previous) => ({ ...previous, error: null, missing: true }));
          return; // nothing to keep polling
        }
        setState((previous) => ({
          ...previous,
          error: error instanceof Error ? error.message : "Could not reach the server",
        }));
        delay = RETRY_MS;
      }
      timer = setTimeout(tick, delay);
    }
    void tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobId]);

  return state;
}
