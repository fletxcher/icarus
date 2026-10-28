/** Shapes returned by the backend's /api/simulations endpoints (backend/src/api/compute). */

export type SimulationStatus = "queued" | "running" | "completed" | "failed";

export type SimulationCase = {
  /** Missing on jobs from before FEA existed: those are CFD. */
  kind?: "cfd";
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

/** The inputs of an FEA job (backend FeaCase): an uploaded solid and its loads. */
export type FeaCase = {
  kind: "fea";
  geometry_file_id: string;
  geometry_name: string | null;
  units: string;
  material: string;
  mesh: string;
  support_axis: string;
  support_side: string;
  spin_rpm: number;
  spin_axis_origin: number[];
  spin_axis_direction: number[];
  pressure_mpa: number;
  gas_temperature_c: number | null;
  htc_w_m2k: number;
  base_temperature_c: number;
  modal: boolean;
};

/** A job's inputs: CFD or FEA, told apart by `kind`. */
export type JobCase = SimulationCase | FeaCase;

/** One row of the simulations list (GET /api/simulations). */
export type SimulationSummary = {
  id: string;
  thread_id: string | null;
  case: JobCase;
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

/** What an FEA job reports (workstation/fea_case.py `build_results`). */
export type FeaResult = {
  material: string;
  elements: number;
  nodes: number;
  volume_mm3: number;
  mass_kg: number;
  bounding_box_mm: number[][];
  max_displacement_mm: number;
  max_displacement_at_mm: number[];
  max_von_mises_mpa: number;
  max_von_mises_at_mm: number[];
  max_von_mises_away_from_support_mpa: number;
  max_von_mises_away_at_mm: number[];
  typical_von_mises_mpa: number;
  min_safety_factor_yield_away_from_support: number;
  min_safety_factor_yield_anywhere: number;
  peak_stress_note: string;
  temperature_c?: number[];
  natural_frequencies_hz?: number[];
  engine_order_hz?: Record<string, number>;
  warnings?: string[];
};

/** What the monitor gets from an FEA job while it runs. */
export type FeaProgress = {
  status: SimulationStatus;
  stage: string;
  fraction: number;
  eta_s: number | null;
  lines: string[];
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
  /** Images and the flow video rendered after the solve (workstation/render_video.py). */
  media?: SimulationMedia[];
  [key: string]: unknown;
};

export type Simulation = {
  id: string;
  thread_id: string | null;
  /** Taken off the simulations list; the job and its results are kept. */
  hidden?: boolean;
  case: JobCase;
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

/** CFD runs started in a period (GET /api/simulations/usage). */
export type UsagePeriod = {
  runs: number;
  completed: number;
  failed: number;
  running: number;
  /** Seconds from start to being seen finished, summed over finished runs (approximate). */
  compute_s: number;
  by_shape: Record<string, number>;
};

export type SimulationUsage = { week: UsagePeriod; month: UsagePeriod; all_time: UsagePeriod };
