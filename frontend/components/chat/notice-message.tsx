"use client";

import Link from "next/link";
import { ActivityIcon, BellIcon, MessagesSquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Message } from "@/lib/chat/types";
import { discussNotice } from "@/stores/chat-actions";
import { useIsGenerating } from "@/stores/chat-store";

const JOB_ID =
  /Simulation ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) finished/i;

/**
 * An automatic note in the chat: the backend adds one when a simulation ends, and the assistant
 * reads it with the next message, so nobody has to ask whether the job is done.
 */
export function NoticeMessage({
  threadId,
  message,
  isLast,
}: {
  threadId: string;
  message: Message;
  isLast: boolean;
}) {
  const isGenerating = useIsGenerating();
  const jobId = JOB_ID.exec(message.content)?.[1];
  // The notice's first sentence: the rest is for the assistant.
  const summary = message.content.split("\n")[0].split(". ")[0];

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-muted/40 px-3 py-2.5 text-sm">
      <BellIcon className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 break-words">{summary}</span>
      <div className="flex gap-2">
        {jobId && (
          <Button variant="outline" size="sm" asChild>
            <Link href={`/simulations/${jobId}`}>
              <ActivityIcon />
              Open simulation
            </Link>
          </Button>
        )}
        {isLast && (
          <Button
            variant="outline"
            size="sm"
            disabled={isGenerating}
            onClick={() => void discussNotice(threadId)}
          >
            <MessagesSquareIcon />
            Discuss the results
          </Button>
        )}
      </div>
    </div>
  );
}
