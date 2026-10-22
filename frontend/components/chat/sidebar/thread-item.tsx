"use client";

import { useRef, useState } from "react";
import { MoreHorizontalIcon, PencilIcon, PinIcon, PinOffIcon, Trash2Icon } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  SidebarInput,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import type { Thread } from "@/lib/chat/types";
import { useChatStore } from "@/stores/chat-store";

export function ThreadItem({ thread, isActive }: { thread: Thread; isActive: boolean }) {
  const { isMobile, setOpenMobile } = useSidebar();
  const selectThread = useChatStore((state) => state.selectThread);
  const renameThread = useChatStore((state) => state.renameThread);
  const togglePin = useChatStore((state) => state.togglePin);
  const deleteThread = useChatStore((state) => state.deleteThread);
  const [isEditing, setIsEditing] = useState(false);
  const [isConfirmingDelete, setIsConfirmingDelete] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const commitRename = (value: string) => {
    setIsEditing(false);
    const title = value.trim();
    if (title && title !== thread.title) renameThread(thread.id, title);
  };

  const handleSelect = () => {
    selectThread(thread.id);
    if (isMobile) setOpenMobile(false);
  };

  return (
    <SidebarMenuItem>
      {isEditing ? (
        <SidebarInput
          ref={inputRef}
          defaultValue={thread.title}
          aria-label="Chat title"
          className="h-8"
          onFocus={(event) => event.currentTarget.select()}
          onBlur={(event) => commitRename(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") commitRename(event.currentTarget.value);
            if (event.key === "Escape") setIsEditing(false);
          }}
        />
      ) : (
        <SidebarMenuButton isActive={isActive} onClick={handleSelect}>
          <span>{thread.title}</span>
        </SidebarMenuButton>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuAction showOnHover hidden={isEditing}>
            <MoreHorizontalIcon />
            <span className="sr-only">More</span>
          </SidebarMenuAction>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side={isMobile ? "bottom" : "right"}
          align="start"
          // The menu traps focus until it closes, so focus the rename input from here.
          onCloseAutoFocus={(event) => {
            if (!inputRef.current) return;
            event.preventDefault();
            inputRef.current.focus();
          }}
        >
          <DropdownMenuItem onSelect={() => setIsEditing(true)}>
            <PencilIcon />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => togglePin(thread.id)}>
            {thread.pinnedAt === null ? <PinIcon /> : <PinOffIcon />}
            {thread.pinnedAt === null ? "Pin" : "Unpin"}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => setIsConfirmingDelete(true)}>
            <Trash2Icon />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={isConfirmingDelete} onOpenChange={setIsConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete chat?</AlertDialogTitle>
            <AlertDialogDescription>
              &ldquo;{thread.title}&rdquo; will be permanently deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => deleteThread(thread.id)}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SidebarMenuItem>
  );
}
