import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { PaletteName } from "@/lib/palettes";

type PaletteState = {
  palette: PaletteName;
  setPalette: (palette: PaletteName) => void;
};

/**
 * The colour map of the login page's flow, remembered in this browser. It isn't read from
 * storage until the page has mounted (`persist.rehydrate()`), so the server-rendered page and
 * the first client render agree.
 */
export const usePalette = create<PaletteState>()(
  persist(
    (set) => ({
      palette: "viridis",
      setPalette: (palette) => set({ palette }),
    }),
    { name: "icarus-palette", skipHydration: true },
  ),
);
