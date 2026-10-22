import { useEffect, useSyncExternalStore } from "react";
import { useChatStore } from "@/stores/chat-store";

// Read lazily: persist isn't attached on the server, where localStorage doesn't exist.
const subscribe = (onChange: () => void) => useChatStore.persist.onFinishHydration(onChange);
const getSnapshot = () => useChatStore.persist.hasHydrated();
const getServerSnapshot = () => false;

/** Loads persisted chats from localStorage after mount and reports when they're ready. */
export function useChatHydrated(): boolean {
  const hydrated = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  useEffect(() => {
    if (!useChatStore.persist.hasHydrated()) void useChatStore.persist.rehydrate();
  }, []);

  return hydrated;
}
