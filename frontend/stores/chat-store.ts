import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { DEFAULT_MODEL_ID } from "@/lib/chat/models";
import type { Message, Thread } from "@/lib/chat/types";

type ChatState = {
  threads: Record<string, Thread>;
  activeThreadId: string | null;
  model: string;
  webSearch: boolean;
  draft: string;
  /** Set while a reply is streaming; not persisted. */
  controller: AbortController | null;
};

type ChatActions = {
  newChat: () => void;
  selectThread: (id: string) => void;
  createThread: (title: string) => string;
  renameThread: (id: string, title: string) => void;
  togglePin: (id: string) => void;
  deleteThread: (id: string) => void;
  addMessage: (threadId: string, message: Message) => void;
  updateMessage: (threadId: string, messageId: string, patch: Partial<Message>) => void;
  truncateFrom: (threadId: string, messageId: string) => void;
  setModel: (model: string) => void;
  setWebSearch: (enabled: boolean) => void;
  setDraft: (draft: string) => void;
  setController: (controller: AbortController | null) => void;
};

function updateThread(
  threads: Record<string, Thread>,
  id: string,
  update: (thread: Thread) => Partial<Thread>,
): Record<string, Thread> {
  const thread = threads[id];
  if (!thread) return threads;
  return { ...threads, [id]: { ...thread, ...update(thread) } };
}

export const useChatStore = create<ChatState & ChatActions>()(
  persist(
    (set) => ({
      threads: {},
      activeThreadId: null,
      model: DEFAULT_MODEL_ID,
      webSearch: false,
      draft: "",
      controller: null,

      newChat: () => set({ activeThreadId: null }),

      selectThread: (id) => set({ activeThreadId: id }),

      createThread: (title) => {
        const now = Date.now();
        const thread: Thread = {
          id: crypto.randomUUID(),
          title,
          createdAt: now,
          updatedAt: now,
          pinnedAt: null,
          messages: [],
        };
        set((state) => ({
          threads: { ...state.threads, [thread.id]: thread },
          activeThreadId: thread.id,
        }));
        return thread.id;
      },

      renameThread: (id, title) =>
        set((state) => ({ threads: updateThread(state.threads, id, () => ({ title })) })),

      togglePin: (id) =>
        set((state) => ({
          threads: updateThread(state.threads, id, (thread) => ({
            pinnedAt: thread.pinnedAt === null ? Date.now() : null,
          })),
        })),

      deleteThread: (id) =>
        set((state) => {
          const threads = { ...state.threads };
          delete threads[id];
          return {
            threads,
            activeThreadId: state.activeThreadId === id ? null : state.activeThreadId,
          };
        }),

      addMessage: (threadId, message) =>
        set((state) => ({
          threads: updateThread(state.threads, threadId, (thread) => ({
            messages: [...thread.messages, message],
            updatedAt: message.createdAt,
          })),
        })),

      updateMessage: (threadId, messageId, patch) =>
        set((state) => ({
          threads: updateThread(state.threads, threadId, (thread) => ({
            messages: thread.messages.map((message) =>
              message.id === messageId ? { ...message, ...patch } : message,
            ),
          })),
        })),

      truncateFrom: (threadId, messageId) =>
        set((state) => ({
          threads: updateThread(state.threads, threadId, (thread) => {
            const index = thread.messages.findIndex((message) => message.id === messageId);
            return index === -1 ? {} : { messages: thread.messages.slice(0, index) };
          }),
        })),

      setModel: (model) => set({ model }),
      setWebSearch: (webSearch) => set({ webSearch }),
      setDraft: (draft) => set({ draft }),
      setController: (controller) => set({ controller }),
    }),
    {
      name: "icarus-chat",
      storage: createJSONStorage(() => localStorage),
      // Rehydrated manually on mount so server and first client render match.
      skipHydration: true,
      partialize: ({ threads, activeThreadId, model, webSearch, draft }) => ({
        threads,
        activeThreadId,
        model,
        webSearch,
        draft,
      }),
      // A reply that was streaming when the page closed can never finish.
      merge: (persisted, current) => {
        // `persisted` is undefined on first visit, before anything has been saved.
        const state = (persisted ?? {}) as Partial<ChatState>;
        const threads = Object.fromEntries(
          Object.entries(state.threads ?? {}).map(([id, thread]) => [
            id,
            {
              ...thread,
              messages: thread.messages.map((message) =>
                message.status === "pending" || message.status === "streaming"
                  ? { ...message, status: "stopped" as const }
                  : message,
              ),
            },
          ]),
        );
        return { ...current, ...state, threads };
      },
    },
  ),
);

export function useActiveThread(): Thread | null {
  return useChatStore((state) =>
    state.activeThreadId ? (state.threads[state.activeThreadId] ?? null) : null,
  );
}

export function useIsGenerating(): boolean {
  return useChatStore((state) => state.controller !== null);
}
