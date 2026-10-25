"use client";

import { ChartLineIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useChatStore } from "@/stores/chat-store";

/** Top-right button that opens the simulations panel; a dot counts the running jobs. */
export function SimulationsButton() {
  const open = useChatStore((state) => state.simulationsOpen);
  const toggle = useChatStore((state) => state.toggleSimulations);
  const running = useChatStore(
    (state) => state.simulations.filter((job) => job.status === "running" || job.status === "queued").length,
  );

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant={open ? "secondary" : "ghost"}
          size="icon-sm"
          className="relative"
          aria-label="Simulations"
          aria-pressed={open}
          onClick={toggle}
        >
          <ChartLineIcon />
          {running > 0 && (
            <span className="absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground tabular-nums">
              {running}
            </span>
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>Simulations</TooltipContent>
    </Tooltip>
  );
}
