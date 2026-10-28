"use client";

import { useState } from "react";
import { CheckIcon, CopyIcon, PencilIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useCopy } from "@/hooks/use-copy";
import type { Message } from "@/lib/chat/types";
import { editMessage } from "@/stores/chat-actions";
import { useIsGenerating } from "@/stores/chat-store";
import { ActionButton } from "./action-button";
import { AttachmentChip } from "./attachment-chip";

export function UserMessage({ threadId, message }: { threadId: string; message: Message }) {
  const { copied, copy } = useCopy();
  const isGenerating = useIsGenerating();
  const [isEditing, setIsEditing] = useState(false);

  if (isEditing) {
    return (
      <EditMessage
        initialValue={message.content}
        onCancel={() => setIsEditing(false)}
        onSave={(value) => {
          setIsEditing(false);
          void editMessage(threadId, message.id, value, message.attachments);
        }}
      />
    );
  }

  return (
    <div className="group flex flex-col items-end gap-1">
      {message.attachments.length > 0 && (
        <div className="flex max-w-[80%] flex-wrap justify-end gap-2">
          {message.attachments.map((attachment) => (
            <AttachmentChip
              key={attachment.id}
              name={attachment.name}
              size={attachment.size}
              fileId={attachment.id}
            />
          ))}
        </div>
      )}
      <div className="max-w-[80%] rounded-2xl bg-muted px-4 py-2.5 text-(length:--transcript-text-size) leading-7 whitespace-pre-wrap break-words">
        {message.content}
      </div>
      <div className="flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <ActionButton label={copied ? "Copied" : "Copy"} onClick={() => copy(message.content)}>
          {copied ? <CheckIcon /> : <CopyIcon />}
        </ActionButton>
        <ActionButton label="Edit" disabled={isGenerating} onClick={() => setIsEditing(true)}>
          <PencilIcon />
        </ActionButton>
      </div>
    </div>
  );
}

function EditMessage({
  initialValue,
  onCancel,
  onSave,
}: {
  initialValue: string;
  onCancel: () => void;
  onSave: (value: string) => void;
}) {
  const [value, setValue] = useState(initialValue);
  const canSave = value.trim().length > 0 && value !== initialValue;

  return (
    <div className="ml-auto flex w-full max-w-[80%] flex-col gap-2 rounded-2xl border bg-background p-2 shadow-xs">
      <Textarea
        autoFocus
        value={value}
        aria-label="Edit message"
        className="max-h-60 min-h-12 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            if (canSave) onSave(value);
          }
        }}
      />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" disabled={!canSave} onClick={() => onSave(value)}>
          Send
        </Button>
      </div>
    </div>
  );
}
