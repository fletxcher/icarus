import type { JobCase } from "@/lib/chat/simulation-types";

const SHAPE_NAMES: Record<string, string> = {
  airfoil: "Airfoil",
  cylinder: "Cylinder",
  ellipse: "Ellipse",
  wedge: "Wedge",
  rectangle: "Rectangle",
  fea: "FEA",
};

export const MATERIAL_NAMES: Record<string, string> = {
  inconel718: "Inconel 718",
  ti6al4v: "Ti-6Al-4V",
  aluminium7075: "Aluminium 7075",
  steel4340: "Steel 4340",
};

export function shapeName(shape: string): string {
  return SHAPE_NAMES[shape] ?? shape;
}

/** A job's display name, e.g. "NACA 2412 · Re 3.0e+6" or "Cylinder · Re 1.0e+2". */
export function jobName(simulation: JobCase): string {
  if (simulation.kind === "fea") {
    const part = simulation.geometry_name?.replace(/\.[^.]+$/, "") ?? "solid";
    return `FEA · ${part} · ${MATERIAL_NAMES[simulation.material] ?? simulation.material}`;
  }
  const body =
    simulation.shape === "airfoil" && simulation.naca
      ? `NACA ${simulation.naca}`
      : (SHAPE_NAMES[simulation.shape] ?? simulation.shape);
  return `${body} · Re ${simulation.reynolds.toExponential(1)}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours) return `${hours} h ${minutes} min`;
  if (minutes) return `${minutes} min ${rest} s`;
  return `${rest} s`;
}

/** The stages of an FEA job (workstation/run_fea.sh), as shown in the list. */
export const FEA_STAGE_LABELS: Record<string, string> = {
  mesh: "Mesh",
  deck: "Setup",
  solve: "Solve",
  modal: "Frequencies",
  results: "Results",
};

export const STAGE_LABELS: Record<string, string> = {
  geo: "Geometry",
  gmsh: "Mesh",
  foam: "Case setup",
  gmshToFoam: "Convert mesh",
  boundary: "Patches",
  checkMesh: "Check mesh",
  decomposePar: "Decompose",
  solver: "Solve",
  reconstruct: "Reconstruct",
  vtk: "Export VTK",
  render: "Render video",
  results: "Results",
};
