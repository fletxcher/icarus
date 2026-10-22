"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowDownIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Thread } from "@/lib/chat/types";
import { AssistantMessage } from "./assistant-message";
import { Composer } from "./composer";
import { UserMessage } from "./user-message";

const STICK_THRESHOLD_PX = 80;

/** Scrollable message list that sticks to the bottom while new content arrives. */
export function ThreadView({ thread }: { thread: Thread }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const lastMessage = thread.messages.at(-1);

  const scrollToBottom = (behavior: ScrollBehavior = "auto") => {
    const element = scrollRef.current;
    element?.scrollTo({ top: element.scrollHeight, behavior });
  };

  // Follow streaming output only while the user hasn't scrolled away.
  useLayoutEffect(() => {
    if (isAtBottom) scrollToBottom();
  }, [lastMessage?.content, lastMessage?.status, isAtBottom]);

  // Always jump to the newest message when one is added.
  useEffect(() => {
    scrollToBottom("smooth");
  }, [thread.messages.length]);

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
          {thread.messages.map((message) =>
            message.role === "user" ? (
              <UserMessage key={message.id} threadId={thread.id} message={message} />
            ) : (
              <AssistantMessage
                key={message.id}
                threadId={thread.id}
                message={message}
                isLast={message === lastMessage}
              />
            ),
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
