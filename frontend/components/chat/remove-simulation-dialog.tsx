"use client";

import { useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { jobName } from "@/lib/chat/simulation-format";
import type { SimulationCase } from "@/lib/chat/simulation-types";
import { hideSimulation } from "@/stores/chat-actions";

/** Asks before taking a simulation off the list. Nothing is deleted: it stays in the chat. */
export function RemoveSimulationDialog({
  open,
  onOpenChange,
  jobId,
  simulation,
  onRemoved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: string;
  simulation: { case: SimulationCase; status: string } | null;
  onRemoved?: () => void;
}) {
  const [removing, setRemoving] = useState(false);
  const running = simulation?.status === "running" || simulation?.status === "queued";

  const confirm = async () => {
    setRemoving(true);
    const removed = await hideSimulation(jobId);
    setRemoving(false);
    if (removed) onRemoved?.();
  };

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove from the list?</AlertDialogTitle>
          <AlertDialogDescription>
            {simulation ? `“${jobName(simulation.case)}” will` : "This simulation will"} no longer appear in the
            simulations list. Its results and files are kept, and you can still open it from the chat
            {running && ", and it keeps running"}.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction disabled={removing} onClick={() => void confirm()}>
            Remove
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
