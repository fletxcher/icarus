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
import { deleteSimulation } from "@/stores/chat-actions";

/** Asks before deleting a simulation: its record, its files, and the run itself if it's going. */
export function DeleteSimulationDialog({
  open,
  onOpenChange,
  jobId,
  simulation,
  onDeleted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: string;
  simulation: { case: SimulationCase; status: string } | null;
  onDeleted?: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const running = simulation?.status === "running" || simulation?.status === "queued";

  const confirm = async () => {
    setDeleting(true);
    const deleted = await deleteSimulation(jobId);
    setDeleting(false);
    if (deleted) onDeleted?.();
  };

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete simulation?</AlertDialogTitle>
          <AlertDialogDescription>
            {simulation ? `“${jobName(simulation.case)}” will be` : "This simulation will be"} permanently
            deleted, including its results, images, video and files on the work station.
            {running && " It is still running, so it will be stopped first."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={deleting} onClick={() => void confirm()}>
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
