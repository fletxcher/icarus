"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { AppSidebar } from "@/components/chat/sidebar/app-sidebar";
import { SimulationsButton } from "@/components/chat/simulations-button";
import { SimulationsPanel } from "@/components/chat/simulations-panel";
import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { useSimulationListPolling } from "@/hooks/use-simulation-list";
import { cn } from "@/lib/utils";
import { useAuth } from "@/stores/auth-store";
import { bootstrap } from "@/stores/chat-actions";
import { useChatStore } from "@/stores/chat-store";

/**
 * The frame every page shares: the chats sidebar on the left, a header with the page title
 * and the simulations button, the page itself, and the simulations list on the right.
 */
export function AppShell({
  title,
  simulationId,
  children,
}: {
  title: string;
  /** The simulation whose page this is, to highlight it in the list. */
  simulationId?: string;
  children: ReactNode;
}) {
  const simulationsOpen = useChatStore((state) => state.simulationsOpen);
  const chatViewRequests = useChatStore((state) => state.chatViewRequests);
  const router = useRouter();
  const seenRequests = useRef(chatViewRequests);
  const { user, load } = useAuth();
  const signedIn = user !== null;
  // Whether this page has asked the server who is signed in yet (what the store remembers
  // from an earlier page may be stale, e.g. "nobody" from before logging in).
  const [checked, setChecked] = useState(false);
  useSimulationListPolling(signedIn);

  // The app is for signed-in people: ask who this is, and send everyone else to log in.
  useEffect(() => {
    void load().then(() => setChecked(true));
  }, [load]);

  useEffect(() => {
    if (checked && !signedIn) router.replace("/login");
  }, [checked, signedIn, router]);

  useEffect(() => {
    if (signedIn) void bootstrap();
  }, [signedIn]);

  // Picking a chat (or "New chat") in the sidebar from another page goes back to the chat.
  useEffect(() => {
    if (chatViewRequests === seenRequests.current) return;
    seenRequests.current = chatViewRequests;
    // Read at the time, not as a hook: usePathname would need a Suspense boundary on
    // routes with a dynamic segment.
    if (window.location.pathname !== "/") router.push("/");
  }, [chatViewRequests, router]);

  // Nothing of the app is shown until the server has said this person is signed in.
  if (!signedIn) return null;

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="h-svh overflow-hidden md:h-[calc(100svh-1rem)]">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
          <SidebarTrigger />
          <Separator
            orientation="vertical"
            className="mr-1 data-vertical:h-4 data-vertical:self-center"
          />
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h1>
          <SimulationsButton />
        </header>
        <div className="flex min-h-0 flex-1">
          {/* On small screens an open list takes the place of the page. */}
          <div
            className={cn("min-w-0 flex-1 flex-col", simulationsOpen ? "hidden md:flex" : "flex")}
          >
            {children}
          </div>
          {simulationsOpen && <SimulationsPanel currentId={simulationId} />}
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
