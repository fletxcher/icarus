"use client";

import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { useChatHydrated } from "@/hooks/use-chat-hydrated";
import { useActiveThread } from "@/stores/chat-store";
import { AppSidebar } from "./sidebar/app-sidebar";
import { ThreadView } from "./thread-view";
import { Welcome } from "./welcome";

export function ChatApp() {
  const hydrated = useChatHydrated();
  const thread = useActiveThread();

  return (
    <SidebarProvider>
      <AppSidebar hydrated={hydrated} />
      <SidebarInset className="h-svh overflow-hidden md:h-[calc(100svh-1rem)]">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
          <SidebarTrigger />
          <Separator orientation="vertical" className="mr-1 data-vertical:h-4 data-vertical:self-center" />
          <h1 className="truncate text-sm font-medium">{thread?.title ?? "New chat"}</h1>
        </header>
        {hydrated &&
          (thread ? <ThreadView key={thread.id} thread={thread} /> : <Welcome />)}
      </SidebarInset>
    </SidebarProvider>
  );
}
