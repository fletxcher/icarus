"use client";

import { CheckIcon, CircleAlertIcon, CopyIcon, RefreshCwIcon } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { useCopy } from "@/hooks/use-copy";
import type { Message, MessagePart } from "@/lib/chat/types";
import { cn } from "@/lib/utils";
import { regenerateReply } from "@/stores/chat-actions";
import { useChatStore, useIsGenerating } from "@/stores/chat-store";
import { ActionButton } from "./action-button";
import { Markdown } from "./markdown";
import { ReasoningBlock } from "./reasoning-block";
import { ToolCallBlock } from "./tool-call-block";

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
  const modelLabel = useChatStore(
    (state) => state.models.find((model) => model.id === message.model)?.label ?? message.model,
  );
  const isStreaming = message.status === "streaming";

  return (
    <div className="flex flex-col gap-3">
      {isStreaming && message.parts.length === 0 && <PendingReply />}
      {message.parts.map((part, index) => (
        <Part
          // Parts only ever get appended, so their position is a stable key.
          key={index}
          part={part}
          isActive={isStreaming && index === message.parts.length - 1}
        />
      ))}

      {message.status === "stopped" && <StatusNote>Generation stopped.</StatusNote>}
      {message.status === "error" && (
        <StatusNote className="text-destructive">
          <CircleAlertIcon className="size-3.5" />
          {message.error ?? "Something went wrong while generating a response."}
        </StatusNote>
      )}

      {!isStreaming && (
        <div className="-mt-2 -ml-2 flex items-center gap-0.5">
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
          {modelLabel && <span className="px-2 text-xs text-muted-foreground">{modelLabel}</span>}
        </div>
      )}
    </div>
  );
}

function Part({ part, isActive }: { part: MessagePart; isActive: boolean }) {
  switch (part.type) {
    case "reasoning":
      return <ReasoningBlock text={part.text} isActive={isActive} />;
    case "tool":
      return <ToolCallBlock part={part} />;
    case "text":
      return <Markdown content={part.text} />;
  }
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
