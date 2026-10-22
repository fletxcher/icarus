"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpIcon, PlusIcon, SquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { addFiles, removeFile, sendMessage, stopGeneration } from "@/stores/chat-actions";
import { useChatStore, useIsGenerating } from "@/stores/chat-store";
import { AttachmentChip } from "./attachment-chip";
import { ModelSelector } from "./model-selector";

// Text files the backend accepts; anything else is rejected with a clear error.
const ACCEPTED_FILES = ".dat,.txt,.csv,.tsv,.json,.md,.xml,.yaml,.yml,.log,text/*";

export function Composer({ placeholder = "Ask anything" }: { placeholder?: string }) {
  const draft = useChatStore((state) => state.draft);
  const setDraft = useChatStore((state) => state.setDraft);
  const pendingFiles = useChatStore((state) => state.pendingFiles);
  const activeThreadId = useChatStore((state) => state.activeThreadId);
  const isGenerating = useIsGenerating();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const isUploading = pendingFiles.some((file) => file.status === "uploading");
  const canSend = draft.trim().length > 0 && !isGenerating && !isUploading;

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
      className={cn(
        "relative w-full rounded-2xl border bg-background shadow-sm transition-shadow focus-within:shadow-md dark:bg-card",
        isDragging && "border-primary/50 ring-3 ring-primary/10",
      )}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setIsDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setIsDragging(false);
        if (event.dataTransfer.files.length) void addFiles([...event.dataTransfer.files]);
      }}
    >
      {pendingFiles.length > 0 && (
        <div className="flex flex-wrap gap-2 px-3 pt-3">
          {pendingFiles.map((file) => (
            <AttachmentChip
              key={file.localId}
              name={file.name}
              size={file.size}
              status={file.status}
              error={file.error}
              onRemove={() => void removeFile(file.localId)}
            />
          ))}
        </div>
      )}

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
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Add files"
                className="text-muted-foreground"
                onClick={() => fileInputRef.current?.click()}
              >
                <PlusIcon />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Add files</TooltipContent>
          </Tooltip>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_FILES}
            className="hidden"
            onChange={(event) => {
              const files = [...(event.target.files ?? [])];
              event.target.value = ""; // Allow picking the same file again.
              if (files.length) void addFiles(files);
            }}
          />
          <ModelSelector />
        </div>

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
