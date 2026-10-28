"use client";

import { useEffect } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PALETTES, PALETTE_NAMES, type PaletteName } from "@/lib/palettes";
import { usePalette } from "@/stores/palette-store";

/** A strip showing the colour map from its low end to its high end. */
function Swatch({ name }: { name: PaletteName }) {
  const stops = PALETTES[name];
  const colours = stops
    .filter((_, index) => index % 3 === 0 || index === stops.length - 1)
    .map(
      ([r, g, b]) => `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`,
    );
  return (
    <span
      aria-hidden
      className="h-3 w-12 shrink-0 rounded-sm"
      style={{ backgroundImage: `linear-gradient(to right, ${colours.join(", ")})` }}
    />
  );
}

/** Picks the colour map of the flow beside the login form. */
export function PaletteMenu() {
  const palette = usePalette((state) => state.palette);
  const setPalette = usePalette((state) => state.setPalette);

  useEffect(() => {
    void usePalette.persist.rehydrate();
  }, []);

  return (
    <Select value={palette} onValueChange={(value) => setPalette(value as PaletteName)}>
      <SelectTrigger
        aria-label="Colour palette"
        size="sm"
        className="w-40 border-white/15 bg-black/40 text-neutral-200 backdrop-blur-sm hover:bg-black/60 dark:border-white/15 dark:bg-black/40 dark:hover:bg-black/60"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end">
        {PALETTE_NAMES.map((name) => (
          <SelectItem key={name} value={name}>
            <Swatch name={name} />
            <span className="capitalize">{name}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
