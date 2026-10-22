"use client";

import { CheckIcon, CircleAlertIcon, CopyIcon, RefreshCwIcon } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { useCopy } from "@/hooks/use-copy";
import { getModel } from "@/lib/chat/models";
import type { Message } from "@/lib/chat/types";
import { cn } from "@/lib/utils";
import { regenerateReply } from "@/stores/chat-actions";
import { useIsGenerating } from "@/stores/chat-store";
import { ActionButton } from "./action-button";
import { Markdown } from "./markdown";

export function AssistantMessage({
  threadId,
  message,
  isLast,
}: {
  threadId: string;
  message: Message;
  isLast: boolean;
}) {
  const { copied, copy } = useCopy();
  const isGenerating = useIsGenerating();
  const isFinished = message.status !== "pending" && message.status !== "streaming";

  return (
    <div className="flex flex-col gap-1">
      {message.status === "pending" ? <PendingReply /> : <Markdown content={message.content} />}

      {message.status === "stopped" && <StatusNote>Generation stopped.</StatusNote>}
      {message.status === "error" && (
        <StatusNote className="text-destructive">
          <CircleAlertIcon className="size-3.5" />
          Something went wrong while generating a response.
        </StatusNote>
      )}

      {isFinished && (
        <div className="-ml-2 flex items-center gap-0.5">
          {message.content && (
            <ActionButton label={copied ? "Copied" : "Copy"} onClick={() => copy(message.content)}>
              {copied ? <CheckIcon /> : <CopyIcon />}
            </ActionButton>
          )}
          {isLast && (
            <ActionButton
              label="Regenerate"
              disabled={isGenerating}
              onClick={() => void regenerateReply(threadId, message.id)}
            >
              <RefreshCwIcon />
            </ActionButton>
          )}
          {message.model && (
            <span className="px-2 text-xs text-muted-foreground">
              {getModel(message.model).label}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function PendingReply() {
  return (
    <div className="flex flex-col gap-2.5 py-2" aria-label="Generating response">
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-[70%]" />
      <Skeleton className="h-4 w-[50%]" />
    </div>
  );
}

function StatusNote({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      className={cn("flex items-center gap-1.5 text-xs text-muted-foreground", className)}
      {...props}
    />
  );
}
