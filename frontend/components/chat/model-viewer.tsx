"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircleIcon } from "lucide-react";
import type * as THREE from "three";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  decodeFloats,
  decodeIndices,
  paletteColour,
  paletteGradient,
  type FeaView,
} from "@/lib/model-view";
import { PALETTE_NAMES, type PaletteName } from "@/lib/palettes";
import { cn } from "@/lib/utils";

export type ModelSource = { type: "stl"; url: string } | { type: "fea"; url: string };

const FIELD_LABELS: Record<string, string> = {
  von_mises: "von Mises stress",
  displacement: "Displacement",
  temperature: "Temperature",
};

type Three = typeof import("three");

/** What the scene holds, so the controls can update it without rebuilding. */
type Scene = {
  THREE: Three;
  mesh: THREE.Mesh;
  /** FEA only: the undeformed positions and displacement vectors, and the part's size. */
  base?: Float32Array;
  vectors?: Float32Array;
  values: Record<string, Float32Array>;
  size: number;
  maxDisplacement: number;
  render: () => void;
};

function format(value: number): string {
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude >= 1e4 || magnitude < 1e-2)) return value.toExponential(1);
  return value.toPrecision(3);
}

async function fetchOk(url: string): Promise<Response> {
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response;
}

/**
 * An interactive 3D view: drag to turn, scroll to zoom. An STL is drawn as a plain solid; an FEA
 * job's surface is coloured by the stress, displacement or temperature it solved for, and can
 * be shown deformed.
 */
export function ModelViewer({ source, className }: { source: ModelSource; className?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const scene = useRef<Scene | null>(null);
  const [view, setView] = useState<FeaView | null>(null);
  // The part's size and largest displacement, for the deformation's exaggeration.
  const [part, setPart] = useState({ size: 1, maxDisplacement: 0 });
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [field, setField] = useState("von_mises");
  const [palette, setPalette] = useState<PaletteName>("turbo");
  const [deformation, setDeformation] = useState(0.5);
  const [fullRange, setFullRange] = useState(false);

  // Build the scene for this source.
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    let cancelled = false;
    let dispose = () => {};

    async function start(host: HTMLDivElement) {
      try {
        const [THREE, { OrbitControls }, { STLLoader }] = await Promise.all([
          import("three"),
          import("three/examples/jsm/controls/OrbitControls.js"),
          import("three/examples/jsm/loaders/STLLoader.js"),
        ]);

        let geometry: THREE.BufferGeometry;
        let loaded: FeaView | null = null;
        let base: Float32Array | undefined;
        let vectors: Float32Array | undefined;
        const values: Record<string, Float32Array> = {};
        if (source.type === "stl") {
          const data = await (await fetchOk(source.url)).arrayBuffer();
          geometry = new STLLoader().parse(data);
        } else {
          loaded = (await (await fetchOk(source.url)).json()) as FeaView;
          base = decodeFloats(loaded.position);
          vectors = decodeFloats(loaded.displacement_vector);
          geometry = new THREE.BufferGeometry();
          geometry.setAttribute("position", new THREE.BufferAttribute(base.slice(), 3));
          geometry.setAttribute(
            "color",
            new THREE.BufferAttribute(new Float32Array(loaded.vertices * 3), 3),
          );
          geometry.setIndex(new THREE.BufferAttribute(decodeIndices(loaded.index), 1));
          for (const [name, data] of Object.entries(loaded.fields)) {
            values[name] = decodeFloats(data.values);
          }
        }
        if (cancelled) return;

        geometry.computeBoundingBox();
        const box = geometry.boundingBox!;
        const centre = box.getCenter(new THREE.Vector3());
        const extent = box.getSize(new THREE.Vector3());
        const size = Math.max(extent.x, extent.y, extent.z) || 1;

        const material = new THREE.MeshStandardMaterial({
          color: source.type === "stl" ? 0x9aa7b8 : 0xffffff,
          vertexColors: source.type === "fea",
          metalness: 0.15,
          roughness: 0.55,
          side: THREE.DoubleSide,
        });
        const mesh = new THREE.Mesh(geometry, material);
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        host.prepend(renderer.domElement);
        renderer.domElement.className = "block size-full";

        const threeScene = new THREE.Scene();
        threeScene.add(mesh);
        threeScene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.1));
        const camera = new THREE.PerspectiveCamera(35, 1, size / 100, size * 100);
        camera.up.set(0, 0, 1); // CAD and STL files are Z-up
        const headlight = new THREE.DirectionalLight(0xffffff, 2);
        camera.add(headlight);
        threeScene.add(camera);
        camera.position.copy(centre).add(new THREE.Vector3(1.25, -1.75, 1.0).multiplyScalar(size));
        const controls = new OrbitControls(camera, renderer.domElement);
        controls.target.copy(centre);
        controls.enableDamping = true;
        controls.update();

        const render = () => renderer.render(threeScene, camera);
        const resize = () => {
          const { clientWidth: width, clientHeight: height } = host;
          if (!width || !height) return;
          renderer.setSize(width, height, false);
          camera.aspect = width / height;
          camera.updateProjectionMatrix();
          render();
        };
        const observer = new ResizeObserver(resize);
        observer.observe(host);
        let frame = 0;
        const loop = () => {
          frame = requestAnimationFrame(loop);
          controls.update();
          render();
        };
        loop();
        resize();

        const maxDisplacement = loaded?.fields.displacement?.max ?? 0;
        scene.current = { THREE, mesh, base, vectors, values, size, maxDisplacement, render };
        dispose = () => {
          cancelAnimationFrame(frame);
          observer.disconnect();
          controls.dispose();
          geometry.dispose();
          material.dispose();
          renderer.dispose();
          renderer.domElement.remove();
          scene.current = null;
        };
        setPart({ size, maxDisplacement });
        setView(loaded);
        setStatus("ready");
      } catch (error) {
        if (cancelled) return;
        setMessage(error instanceof Error ? error.message : "Couldn't load the model");
        setStatus("error");
      }
    }
    void start(element);
    return () => {
      cancelled = true;
      dispose();
    };
  }, [source.type, source.url]);

  const range = useMemo(() => {
    const data = view?.fields[field];
    if (!data) return null;
    const high = fullRange ? data.max : Math.min(data.p98, data.max);
    return {
      low: data.min,
      high: high > data.min ? high : data.max,
      clipped: !fullRange && data.max > data.p98 * 1.05,
    };
  }, [view, field, fullRange]);

  // Colour and deform the surface to match the controls.
  useEffect(() => {
    const current = scene.current;
    if (!current || !view || !range || status !== "ready") return;
    const values = current.values[field];
    if (!values) return;
    const geometry = current.mesh.geometry;
    const colours = geometry.getAttribute("color") as THREE.BufferAttribute;
    const span = range.high - range.low || 1;
    for (let i = 0; i < values.length; i++) {
      const [r, g, b] = paletteColour(palette, (values[i] - range.low) / span);
      colours.setXYZ(i, r, g, b);
    }
    colours.needsUpdate = true;

    const { base, vectors, maxDisplacement, size } = current;
    if (base && vectors) {
      // The deformation is exaggerated so the largest displacement is up to 8% of the part.
      const factor = maxDisplacement > 0 ? (deformation * 0.08 * size) / maxDisplacement : 0;
      const position = geometry.getAttribute("position") as THREE.BufferAttribute;
      for (let i = 0; i < base.length; i++) position.array[i] = base[i] + vectors[i] * factor;
      position.needsUpdate = true;
      geometry.computeVertexNormals();
    }
  }, [view, field, palette, deformation, range, status]);

  const fields = Object.keys(view?.fields ?? {});
  // The deformation is exaggerated so the largest displacement is up to 8% of the part.
  const factor =
    part.maxDisplacement > 0 ? (deformation * 0.08 * part.size) / part.maxDisplacement : 0;

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div ref={container} className="relative h-96 overflow-hidden rounded-lg border bg-muted/30">
        {status === "loading" && (
          <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <LoaderCircleIcon className="size-4 animate-spin" />
            Loading the model…
          </div>
        )}
        {status === "error" && (
          <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-sm text-muted-foreground">
            Couldn&apos;t load the 3D view ({message}).
          </div>
        )}
        {status === "ready" && range && (
          <div className="pointer-events-none absolute bottom-3 left-3 w-56 rounded-md bg-background/80 p-2 text-xs backdrop-blur-sm">
            <div className="mb-1 font-medium">
              {FIELD_LABELS[field] ?? field} ({view!.fields[field].unit})
            </div>
            <div
              className="h-2.5 rounded-sm"
              style={{ backgroundImage: paletteGradient(palette) }}
            />
            <div className="mt-1 flex justify-between tabular-nums text-muted-foreground">
              <span>{format(range.low)}</span>
              <span>
                {format(range.high)}
                {range.clipped ? "+" : ""}
              </span>
            </div>
            {range.clipped && (
              <div className="mt-0.5 text-muted-foreground">
                peak {format(view!.fields[field].max)}, clipped to show the rest
              </div>
            )}
          </div>
        )}
        {status === "ready" && !view && (
          <div className="pointer-events-none absolute bottom-3 left-3 rounded-md bg-background/80 px-2 py-1 text-xs text-muted-foreground backdrop-blur-sm">
            Drag to turn, scroll to zoom
          </div>
        )}
      </div>
      {view && status === "ready" && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <Select value={field} onValueChange={setField}>
            <SelectTrigger size="sm" aria-label="Field" className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {fields.map((name) => (
                <SelectItem key={name} value={name}>
                  {FIELD_LABELS[name] ?? name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={palette} onValueChange={(value) => setPalette(value as PaletteName)}>
            <SelectTrigger size="sm" aria-label="Colour palette" className="w-32 capitalize">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PALETTE_NAMES.map((name) => (
                <SelectItem key={name} value={name} className="capitalize">
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <label className="flex items-center gap-2 text-muted-foreground">
            Deformation
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={deformation}
              onChange={(event) => setDeformation(Number(event.target.value))}
              aria-label="Deformation"
              className="w-28"
            />
            <span className="w-14 tabular-nums">{factor ? `×${format(factor)}` : "off"}</span>
          </label>
          <label className="flex items-center gap-2 text-muted-foreground">
            <input
              type="checkbox"
              checked={fullRange}
              onChange={(event) => setFullRange(event.target.checked)}
            />
            Full colour range
          </label>
        </div>
      )}
    </div>
  );
}
