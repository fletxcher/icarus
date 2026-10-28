import { PALETTES, type PaletteName } from "@/lib/palettes";

/** The surface of an FEA job with its solution on it (workstation/fea_case.py `build_view`). */
export type FeaViewField = {
  /** Base64 little-endian float32, one value per surface vertex. */
  values: string;
  min: number;
  max: number;
  /** Where most of the part lies: the colours are scaled to this unless "full range" is asked. */
  p98: number;
  unit: string;
};

export type FeaView = {
  vertices: number;
  triangles: number;
  position: string;
  displacement_vector: string;
  index: string;
  fields: Record<string, FeaViewField>;
  bbox_min: number[];
  bbox_max: number[];
};

function bytes(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out.buffer;
}

export const decodeFloats = (base64: string) => new Float32Array(bytes(base64));
export const decodeIndices = (base64: string) => new Uint32Array(bytes(base64));

/** The colour at `t` (0 to 1) on a palette, as 0-1 RGB. */
export function paletteColour(palette: PaletteName, t: number): [number, number, number] {
  const stops = PALETTES[palette];
  const x = Math.min(Math.max(t, 0), 1) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  const f = x - i;
  const a = stops[i];
  const b = stops[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

/** A CSS gradient of a palette, low end to high end. */
export function paletteGradient(palette: PaletteName): string {
  const stops = PALETTES[palette];
  const colours = stops
    .filter((_, index) => index % 3 === 0 || index === stops.length - 1)
    .map(
      ([r, g, b]) => `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`,
    );
  return `linear-gradient(to right, ${colours.join(", ")})`;
}
