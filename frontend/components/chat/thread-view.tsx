"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowDownIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Message } from "@/lib/chat/types";
import { useChatStore } from "@/stores/chat-store";
import { AssistantMessage } from "./assistant-message";
import { Composer } from "./composer";
import { UserMessage } from "./user-message";

const STICK_THRESHOLD_PX = 80;

/** Scrollable message list that sticks to the bottom while new content arrives. */
export function ThreadView({ threadId }: { threadId: string }) {
  const messages = useChatStore((state) => state.messages[threadId]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const lastMessage = messages?.at(-1);

  const scrollToBottom = (behavior: ScrollBehavior = "auto") => {
    const element = scrollRef.current;
    element?.scrollTo({ top: element.scrollHeight, behavior });
  };

  // Follow streaming output (text, reasoning, tool calls) while the user hasn't scrolled away.
  useLayoutEffect(() => {
    if (isAtBottom) scrollToBottom();
  }, [lastMessage, isAtBottom]);

  // Always jump to the newest message when one is added.
  useEffect(() => {
    scrollToBottom("smooth");
  }, [messages?.length]);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto"
        onScroll={(event) => {
          const { scrollHeight, scrollTop, clientHeight } = event.currentTarget;
          setIsAtBottom(scrollHeight - scrollTop - clientHeight < STICK_THRESHOLD_PX);
        }}
      >
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 pt-6 pb-10">
          {messages ? (
            <MessageList threadId={threadId} messages={messages} />
          ) : (
            <MessagesSkeleton />
          )}
        </div>
      </div>

      <div className="relative mx-auto w-full max-w-3xl px-4 pb-2">
        <div className="pointer-events-none absolute inset-x-0 -top-10 h-10 bg-linear-to-t from-background to-transparent" />
        {!isAtBottom && (
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Scroll to bottom"
            className="absolute -top-12 left-1/2 -translate-x-1/2 rounded-full shadow-sm"
            onClick={() => scrollToBottom("smooth")}
          >
            <ArrowDownIcon />
          </Button>
        )}
        <Composer placeholder="Ask a follow-up" />
        <p className="py-2 text-center text-xs text-muted-foreground">
          AI can make mistakes. Verify important information.
        </p>
      </div>
    </div>
  );
}

function MessageList({ threadId, messages }: { threadId: string; messages: Message[] }) {
  const lastMessage = messages.at(-1);
  return messages.map((message) =>
    message.role === "user" ? (
      <UserMessage key={message.id} threadId={threadId} message={message} />
    ) : (
      <AssistantMessage
        key={message.id}
        threadId={threadId}
        message={message}
        isLast={message === lastMessage}
      />
    ),
  );
}

function MessagesSkeleton() {
  return (
    <div className="flex flex-col gap-8" aria-label="Loading messages">
      <Skeleton className="ml-auto h-10 w-1/2 rounded-2xl" />
      <div className="flex flex-col gap-2.5">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-[70%]" />
      </div>
    </div>
  );
}
