"use client";

import { useEffect, useRef } from "react";
import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { sendMessage, stopGeneration } from "@/stores/chat-actions";
import { useChatStore, useIsGenerating } from "@/stores/chat-store";
import { ModelSelector } from "./model-selector";

export function Composer({ placeholder = "Ask anything" }: { placeholder?: string }) {
  const draft = useChatStore((state) => state.draft);
  const setDraft = useChatStore((state) => state.setDraft);
  const activeThreadId = useChatStore((state) => state.activeThreadId);
  const isGenerating = useIsGenerating();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const canSend = draft.trim().length > 0 && !isGenerating;

  // Focus the input whenever the user switches chats.
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  }, [activeThreadId]);

  const submit = () => {
    if (canSend) void sendMessage(draft);
  };

  return (
    <form
      className="relative w-full rounded-2xl border bg-background shadow-sm transition-shadow focus-within:shadow-md dark:bg-card"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label htmlFor="composer" className="sr-only">
        Message
      </label>
      <textarea
        id="composer"
        ref={textareaRef}
        rows={1}
        value={draft}
        placeholder={placeholder}
        className="field-sizing-content block max-h-60 min-h-14 w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-[15px] outline-none placeholder:text-muted-foreground"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
      />

      <div className="flex items-center justify-between gap-2 border-t border-dashed px-2 py-2">
        <ModelSelector />

        {isGenerating ? (
          <Button
            type="button"
            size="icon-sm"
            aria-label="Stop generating"
            className="rounded-full"
            onClick={stopGeneration}
          >
            <SquareIcon className="size-3 fill-current" />
          </Button>
        ) : (
          <Button
            type="submit"
            size="icon-sm"
            aria-label="Send message"
            variant={canSend ? "default" : "secondary"}
            disabled={!canSend}
            className="rounded-full"
          >
            <ArrowUpIcon />
          </Button>
        )}
      </div>
    </form>
  );
}
