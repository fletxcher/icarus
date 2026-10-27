import type { SimulationCase } from "@/lib/chat/simulation-types";

const SHAPE_NAMES: Record<string, string> = {
  airfoil: "Airfoil",
  cylinder: "Cylinder",
  ellipse: "Ellipse",
  wedge: "Wedge",
  rectangle: "Rectangle",
};

export function shapeName(shape: string): string {
  return SHAPE_NAMES[shape] ?? shape;
}

/** A job's display name, e.g. "NACA 2412 · Re 3.0e+6" or "Cylinder · Re 1.0e+2". */
export function jobName(simulation: SimulationCase): string {
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
