"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { jobName } from "@/lib/chat/simulation-format";
import type { SimulationSummary } from "@/lib/chat/simulation-types";
import { refreshThread } from "@/stores/chat-actions";
import { useChatStore } from "@/stores/chat-store";

const OPEN_MS = 4000;
const CLOSED_MS = 15000;

const isActive = (job: SimulationSummary) => job.status === "queued" || job.status === "running";

/**
 * Keeps the store's simulation list fresh: often while the simulations panel shows it or the
 * open chat has a job running, rarely otherwise (the header badge only needs the count), and not
 * while the tab is hidden. When a job ends it says so, and the open chat is reloaded so the
 * notice the backend leaves there shows up without a refresh.
 * Does nothing while `enabled` is false (nobody is signed in).
 */
export function useSimulationListPolling(enabled = true) {
  const router = useRouter();
  const setSimulations = useChatStore((state) => state.setSimulations);
  const watching = useChatStore((state) => state.simulationsOpen);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // The jobs that were going at the last look, to notice the ones that have ended since.
    let wasActive = new Set<string>();
    let first = true;

    async function keepOpenChatCurrent(simulations: SimulationSummary[]) {
      const { activeThreadId, controller, threads } = useChatStore.getState();
      // While a reply is streaming the chat is already being updated.
      if (!activeThreadId || controller) return;
      const concerned = simulations.some(
        (job) => job.thread_id === activeThreadId && (isActive(job) || wasActive.has(job.id)),
      );
      if (!concerned) return;
      // A notice bumps the chat's `updated_at`, which the (light) list of chats shows.
      const fresh = (await api.listThreads()).find((thread) => thread.id === activeThreadId);
      const known = threads.find((thread) => thread.id === activeThreadId);
      if (fresh && known && fresh.updated_at !== known.updated_at) {
        await refreshThread(activeThreadId);
      }
    }

    async function tick() {
      let delay = watching ? OPEN_MS : CLOSED_MS;
      if (!document.hidden) {
        try {
          const simulations = await api.listSimulations();
          if (cancelled) return;
          if (!first) {
            for (const job of simulations) {
              if (!wasActive.has(job.id) || isActive(job)) continue;
              const report = job.status === "completed" ? toast.success : toast.error;
              report(`Simulation ${job.status === "completed" ? "finished" : "failed"}`, {
                description: jobName(job.case),
                action: { label: "Open", onClick: () => router.push(`/simulations/${job.id}`) },
              });
            }
          }
          setSimulations(simulations);
          await keepOpenChatCurrent(simulations);
          wasActive = new Set(simulations.filter(isActive).map((job) => job.id));
          first = false;
          const { activeThreadId } = useChatStore.getState();
          if (simulations.some((job) => job.thread_id === activeThreadId && isActive(job))) {
            delay = OPEN_MS;
          }
        } catch {
          // The server may be restarting; the next poll tries again.
        }
      }
      if (!cancelled) timer = setTimeout(tick, delay);
    }
    void tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [enabled, watching, setSimulations, router]);
}
