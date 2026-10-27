import { create } from "zustand";
import { persist } from "zustand/middleware";

export type TextSize = "small" | "medium" | "large";
export type TranscriptWidth = "narrow" | "wide";
export type Motion = "system" | "reduced" | "full";

type SettingsState = {
  textSize: TextSize;
  width: TranscriptWidth;
  motion: Motion;
  set: (
    changes: Partial<Pick<SettingsState, "textSize" | "width" | "motion">>,
  ) => void;
};

/** Display settings, remembered in this browser. */
export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      textSize: "medium",
      width: "narrow",
      motion: "system",
      set: (changes) => set(changes),
    }),
    { name: "icarus-settings" },
  ),
);

/** Whether animation should be off, from the setting and then the system's preference. */
export function prefersReducedMotion(motion: Motion): boolean {
  if (motion !== "system") return motion === "reduced";
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
