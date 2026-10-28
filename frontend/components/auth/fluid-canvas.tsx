"use client";

import { useEffect, useRef } from "react";

import { usePalette } from "@/stores/palette-store";
import { prefersReducedMotion, useSettings } from "@/stores/settings-store";

import { FluidSimulation } from "./fluid-simulation";

/** A canvas of fluid that the pointer stirs, filling its (positioned) parent. */
export function FluidCanvas({ className }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const simulationRef = useRef<FluidSimulation | null>(null);
  const palette = usePalette((state) => state.palette);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // With reduced motion the panel stays a plain dark surface.
    if (prefersReducedMotion(useSettings.getState().motion)) return;
    const simulation = FluidSimulation.create(canvas, usePalette.getState().palette);
    if (!simulation) return;
    simulationRef.current = simulation;

    const position = (event: PointerEvent): [number, number] => {
      const box = canvas.getBoundingClientRect();
      return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height];
    };
    const onEnter = (event: PointerEvent) => simulation.pointerAt(...position(event));
    const onMove = (event: PointerEvent) => simulation.pointerMoved(...position(event));
    const resizer = new ResizeObserver(() => simulation.resize());

    canvas.addEventListener("pointerenter", onEnter);
    canvas.addEventListener("pointermove", onMove);
    resizer.observe(canvas);
    simulation.start();
    return () => {
      canvas.removeEventListener("pointerenter", onEnter);
      canvas.removeEventListener("pointermove", onMove);
      resizer.disconnect();
      simulation.dispose();
      simulationRef.current = null;
    };
  }, []);

  // Another colour map applies from the next frame, without restarting the flow.
  useEffect(() => {
    simulationRef.current?.setPalette(palette);
  }, [palette]);

  return <canvas ref={canvasRef} className={className} style={{ touchAction: "none" }} />;
}
