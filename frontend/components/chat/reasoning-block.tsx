"use client";

import { useState } from "react";
import { BrainIcon, ChevronRightIcon } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

/** The model's thinking: open while it streams, tucked away once the answer starts. */
export function ReasoningBlock({ text, isActive }: { text: string; isActive: boolean }) {
  // null follows isActive until the user opens or closes it themselves.
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? isActive;

  return (
    <Collapsible open={open} onOpenChange={setUserOpen}>
      <CollapsibleTrigger className="group flex items-center gap-1.5 rounded-md py-1 text-sm text-muted-foreground transition-colors hover:text-foreground">
        <BrainIcon className="size-4" />
        <span className={cn(isActive && "animate-pulse")}>{isActive ? "Thinking…" : "Reasoning"}</span>
        <ChevronRightIcon className="size-3.5 transition-transform group-data-[state=open]:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 border-l-2 pl-4 text-sm leading-6 whitespace-pre-wrap text-muted-foreground">
          {text}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
