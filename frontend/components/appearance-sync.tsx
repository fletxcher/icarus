"use client";

import { useEffect } from "react";
import { useSettings } from "@/stores/settings-store";

/** Puts the display settings on <html>, where the stylesheet reads them. */
export function AppearanceSync() {
  const { textSize, width } = useSettings();

  useEffect(() => {
    document.documentElement.dataset.textSize = textSize;
    document.documentElement.dataset.width = width;
  }, [textSize, width]);

  return null;
}
