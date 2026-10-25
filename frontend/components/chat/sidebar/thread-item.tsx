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
import { deleteThread, renameThread, selectThread, togglePin } from "@/stores/chat-actions";

export function ThreadItem({ thread, isActive }: { thread: Thread; isActive: boolean }) {
  const { isMobile, setOpenMobile } = useSidebar();
  const [isEditing, setIsEditing] = useState(false);
  const [isConfirmingDelete, setIsConfirmingDelete] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const commitRename = (value: string) => {
    setIsEditing(false);
    const title = value.trim();
    if (title && title !== thread.title) void renameThread(thread.id, title);
  };

  const handleSelect = () => {
    void selectThread(thread.id);
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
          <DropdownMenuItem onSelect={() => void togglePin(thread.id)}>
            {thread.pinned_at === null ? <PinIcon /> : <PinOffIcon />}
            {thread.pinned_at === null ? "Pin" : "Unpin"}
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
              &ldquo;{thread.title}&rdquo; will be permanently deleted, along with any simulations
              started in it and their results.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void deleteThread(thread.id)}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SidebarMenuItem>
  );
}
