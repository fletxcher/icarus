"use client"

import { useEffect, useRef } from "react"

import { FluidSimulation } from "./fluid-simulation"

/** A canvas of fluid that the pointer stirs, filling its (positioned) parent. */
export function FluidCanvas({ className }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const simulation = FluidSimulation.create(canvas)
    if (!simulation) return

    const position = (event: PointerEvent): [number, number] => {
      const box = canvas.getBoundingClientRect()
      return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height]
    }
    const onEnter = (event: PointerEvent) => simulation.pointerAt(...position(event))
    const onMove = (event: PointerEvent) => simulation.pointerMoved(...position(event))
    const resizer = new ResizeObserver(() => simulation.resize())

    canvas.addEventListener("pointerenter", onEnter)
    canvas.addEventListener("pointermove", onMove)
    resizer.observe(canvas)
    simulation.start()
    return () => {
      canvas.removeEventListener("pointerenter", onEnter)
      canvas.removeEventListener("pointermove", onMove)
      resizer.disconnect()
      simulation.dispose()
    }
  }, [])

  return <canvas ref={canvasRef} className={className} style={{ touchAction: "none" }} />
}
