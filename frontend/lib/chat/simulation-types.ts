/** Shapes returned by the backend's /api/simulations endpoints (backend/src/api/compute). */

export type SimulationStatus = "queued" | "running" | "completed" | "failed";

export type SimulationCase = {
  shape: string;
  naca?: string | null;
  aspect_ratio?: number | null;
  half_angle?: number | null;
  alpha: number;
  reynolds: number;
  mach: number;
  mesh: string;
  wall_type: string;
  wall_velocity_x: number;
  wall_velocity_y: number;
  spin_ratio: number;
  roughness_height: number;
  regime: string;
  video: boolean;
};

/** One row of the simulations list (GET /api/simulations). */
export type SimulationSummary = {
  id: string;
  thread_id: string | null;
  case: SimulationCase;
  status: SimulationStatus;
  error: string | null;
  created_at: string;
  stage: string | null;
  /** 0 to 1 for a running job whose progress is known. */
  fraction: number | null;
  eta_s: number | null;
};

export type SimulationMedia = {
  file: string;
  title: string;
  kind: "image" | "video";
  frames?: number;
  fps?: number;
  duration_s?: number;
};

export type SimulationResult = {
  cl: number;
  cd: number;
  // Everything below is missing from the results of jobs run by older versions.
  cm?: number;
  cl_over_cd?: number | null;
  strouhal?: number;
  cl_rms?: number;
  converged?: boolean;
  iterations?: number;
  cells?: number;
  averaged_over?: string;
  coefficient_basis?: string;
  warnings?: string[];
  y_plus_body?: { min: number; max: number; average: number };
  /** Images and the flow video rendered after the solve (ws/render_video.py). */
  media?: SimulationMedia[];
  [key: string]: unknown;
};

export type Simulation = {
  id: string;
  thread_id: string | null;
  /** Taken off the simulations list; the job and its results are kept. */
  hidden?: boolean;
  case: SimulationCase;
  status: SimulationStatus;
  result: SimulationResult | null;
  error: string | null;
};

export type SimulationMeshQuality = {
  cells?: number | null;
  points?: number | null;
  max_non_orthogonality?: number | null;
  max_skewness?: number | null;
  max_aspect_ratio?: number | null;
  ok?: boolean;
};

/** Static facts about a job: fetched again whenever the run moves to a new stage. */
export type SimulationInfo = {
  case: SimulationCase;
  regime: "steady" | "transient";
  solver: string;
  turbulence: string;
  processors: number | null;
  horizon: number;
  horizon_unit: string;
  mesh: SimulationMeshQuality;
  dictionaries: Record<string, string>;
  stages_expected: string[];
};

/** What the run did since the offsets the previous call returned. */
export type SimulationProgress = {
  status: SimulationStatus;
  log_offset: number;
  coeff_offset: number;
  steps: {
    time: number[];
    courant_max: (number | null)[];
    delta_t: (number | null)[];
    residuals: Record<string, (number | null)[]>;
  };
  coefficients: { time: number[]; cl: number[]; cd: number[]; cm: number[] };
  stage: string | null;
  stages: string[];
  latest_time: number | null;
  horizon: number;
  horizon_unit: string;
  fraction: number | null;
  elapsed_s: number;
  solver_elapsed_s: number | null;
  eta_s: number | null;
  tail: string[];
};

/** Everything accumulated over a run, which is what the panel draws. */
export type MonitorData = {
  time: number[];
  courant: (number | null)[];
  residuals: Record<string, (number | null)[]>;
  coeffTime: number[];
  cl: number[];
  cd: number[];
  cm: number[];
};
