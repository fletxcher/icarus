"use client";

import { AppShell } from "@/components/app-shell";
import { useActiveThread, useChatStore } from "@/stores/chat-store";
import { ThreadView } from "./thread-view";
import { Welcome } from "./welcome";

export function ChatApp() {
  const status = useChatStore((state) => state.threadsStatus);
  const thread = useActiveThread();

  return (
    <AppShell title={thread?.title ?? "New chat"}>
      {status !== "loading" &&
        (thread ? <ThreadView key={thread.id} threadId={thread.id} /> : <Welcome />)}
    </AppShell>
  );
}
