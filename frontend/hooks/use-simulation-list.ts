"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";
import { useChatStore } from "@/stores/chat-store";

const OPEN_MS = 4000;
const CLOSED_MS = 15000;

/**
 * Keeps the store's simulation list fresh: often while the simulations panel shows it,
 * rarely otherwise (the header badge only needs the count), and not while the tab is hidden.
 */
export function useSimulationListPolling() {
  const setSimulations = useChatStore((state) => state.setSimulations);
  const watching = useChatStore((state) => state.simulationsOpen);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function tick() {
      if (!document.hidden) {
        try {
          const simulations = await api.listSimulations();
          if (!cancelled) setSimulations(simulations);
        } catch {
          // The server may be restarting; the next poll tries again.
        }
      }
      if (!cancelled) timer = setTimeout(tick, watching ? OPEN_MS : CLOSED_MS);
    }
    void tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [watching, setSimulations]);
}
