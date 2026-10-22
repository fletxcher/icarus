"use client";

import { useEffect, useMemo } from "react";
import { MessageSquareIcon, PlusIcon } from "lucide-react";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { newChat, selectThread } from "@/stores/chat-actions";
import { useChatStore } from "@/stores/chat-store";

export function SearchCommand({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const threads = useChatStore((state) => state.threads);

  const recentThreads = useMemo(
    () => [...threads].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [threads],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        onOpenChange(!open);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  const run = (action: () => void) => {
    action();
    onOpenChange(false);
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Search chats"
      description="Search chats or start a new one."
    >
      <Command>
        <CommandInput placeholder="Search chats..." />
        <CommandList>
          <CommandEmpty>No chats found.</CommandEmpty>
          <CommandGroup heading="Actions">
            <CommandItem onSelect={() => run(newChat)}>
              <PlusIcon />
              New chat
              <CommandShortcut>⇧⌘O</CommandShortcut>
            </CommandItem>
          </CommandGroup>
          {recentThreads.length > 0 && (
            <CommandGroup heading="Chats">
              {recentThreads.map((thread) => (
                <CommandItem
                  key={thread.id}
                  value={`${thread.title} ${thread.id}`}
                  onSelect={() => run(() => void selectThread(thread.id))}
                >
                  <MessageSquareIcon />
                  <span className="truncate">{thread.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
