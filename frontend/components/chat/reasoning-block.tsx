"use client";

import { useState } from "react";
import { BrainIcon, ChevronRightIcon } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { Markdown } from "./markdown";

/**
 * The model's thinking, collapsed unless the user opens it. Closed, its text isn't rendered at all,
 * so a long stream of reasoning doesn't re-parse Markdown on every update.
 */
export function ReasoningBlock({ text, isActive }: { text: string; isActive: boolean }) {
  const [open, setOpen] = useState(false);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="group flex items-center gap-1.5 rounded-md py-1 text-sm text-muted-foreground transition-colors hover:text-foreground">
        <BrainIcon className="size-4" />
        <span className={cn(isActive && "animate-pulse")}>{isActive ? "Thinking…" : "Reasoning"}</span>
        <ChevronRightIcon className="size-3.5 transition-transform group-data-[state=open]:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 border-l-2 pl-4">
          <Markdown
            content={text}
            className="text-sm leading-6 [--tw-prose-body:var(--color-muted-foreground)] prose-p:my-2"
          />
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
