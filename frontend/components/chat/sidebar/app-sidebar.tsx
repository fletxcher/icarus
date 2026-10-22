"use client";

import { useEffect, useMemo, useState } from "react";
import { FeatherIcon, PlusIcon, SearchIcon } from "lucide-react";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";
import { groupThreads } from "@/lib/chat/group-threads";
import { newChat } from "@/stores/chat-actions";
import { useChatStore } from "@/stores/chat-store";
import { NavUser } from "./nav-user";
import { SearchCommand } from "./search-command";
import { ThreadItem } from "./thread-item";

export function AppSidebar() {
  const status = useChatStore((state) => state.threadsStatus);
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "o" && event.shiftKey && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        newChat();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <Sidebar variant="inset" collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" onClick={newChat} className="gap-2.5">
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                <FeatherIcon className="size-4" />
              </div>
              <span className="text-base font-semibold tracking-tight">Icarus</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton tooltip="New chat" onClick={newChat}>
              <PlusIcon />
              <span>New chat</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              tooltip="Search"
              onClick={() => setIsSearchOpen(true)}
              className="text-sidebar-foreground/80"
            >
              <SearchIcon />
              <span>Search</span>
              <KbdGroup className="ml-auto group-data-[collapsible=icon]:hidden">
                <Kbd>⌘</Kbd>
                <Kbd>K</Kbd>
              </KbdGroup>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent className="group-data-[collapsible=icon]:invisible">
        {status === "loading" && <ThreadGroupsSkeleton />}
        {status === "error" && (
          <p className="px-4 py-2 text-xs text-destructive">Couldn&apos;t load conversations.</p>
        )}
        {status === "ready" && <ThreadGroups />}
      </SidebarContent>

      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
      <SidebarRail />

      <SearchCommand open={isSearchOpen} onOpenChange={setIsSearchOpen} />
    </Sidebar>
  );
}

function ThreadGroups() {
  const threads = useChatStore((state) => state.threads);
  const activeThreadId = useChatStore((state) => state.activeThreadId);
  const groups = useMemo(() => groupThreads(threads), [threads]);

  if (groups.length === 0) {
    return (
      <p className="px-4 py-2 text-xs text-muted-foreground">
        Your conversations will appear here.
      </p>
    );
  }

  return groups.map((group) => (
    <SidebarGroup key={group.label} className="py-1">
      <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {group.threads.map((thread) => (
            <ThreadItem key={thread.id} thread={thread} isActive={thread.id === activeThreadId} />
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  ));
}

// Fixed widths: SidebarMenuSkeleton's random widths would mismatch during hydration.
const SKELETON_WIDTHS = ["80%", "65%", "72%", "55%", "68%"];

function ThreadGroupsSkeleton() {
  return (
    <SidebarGroup>
      <SidebarMenu>
        {SKELETON_WIDTHS.map((width) => (
          <SidebarMenuItem key={width} className="flex h-8 items-center px-2">
            <Skeleton className="h-4" style={{ width }} />
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  );
}
