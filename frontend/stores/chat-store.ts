import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { SimulationSummary } from "@/lib/chat/simulation-types";
import type { Attachment, Message, Model, Thread } from "@/lib/chat/types";

export type LoadStatus = "loading" | "ready" | "error";

/** A file added to the composer; uploaded right away, sent with the next message. */
export type PendingFile = {
  localId: string;
  name: string;
  size: number;
  status: "uploading" | "ready" | "error";
  attachment?: Attachment;
  error?: string;
};

type ChatState = {
  // Server data, loaded from the API.
  threads: Thread[];
  threadsStatus: LoadStatus;
  /** Messages per thread, present once that thread has been opened. */
  messages: Record<string, Message[]>;
  models: Model[];
  // UI state; the last three are remembered in this browser.
  activeThreadId: string | null;
  model: string | null;
  draft: string;
  pendingFiles: PendingFile[];
  /** Set while a reply is streaming. */
  controller: AbortController | null;
  /** The simulations list panel on the right. */
  simulationsOpen: boolean;
  simulations: SimulationSummary[];
  /** Counts picks of a chat in the sidebar, so other pages know to go back to the chat. */
  chatViewRequests: number;
};

type ChatActions = {
  setThreads: (threads: Thread[], status?: LoadStatus) => void;
  setThreadsStatus: (status: LoadStatus) => void;
  upsertThread: (thread: Thread) => void;
  removeThread: (id: string) => void;
  setMessages: (threadId: string, messages: Message[]) => void;
  updateMessages: (threadId: string, update: (messages: Message[]) => Message[]) => void;
  setModels: (models: Model[]) => void;
  setActiveThread: (id: string | null) => void;
  setModel: (model: string) => void;
  setDraft: (draft: string) => void;
  addPendingFile: (file: PendingFile) => void;
  updatePendingFile: (localId: string, changes: Partial<PendingFile>) => void;
  removePendingFile: (localId: string) => void;
  clearPendingFiles: () => void;
  setController: (controller: AbortController | null) => void;
  setSimulations: (simulations: SimulationSummary[]) => void;
  toggleSimulations: () => void;
  closeSimulations: () => void;
  requestChatView: () => void;
};

export const useChatStore = create<ChatState & ChatActions>()(
  persist(
    (set) => ({
      threads: [],
      threadsStatus: "loading",
      messages: {},
      models: [],
      activeThreadId: null,
      model: null,
      draft: "",
      pendingFiles: [],
      controller: null,
      simulationsOpen: false,
      simulations: [],
      chatViewRequests: 0,

      setThreads: (threads, threadsStatus = "ready") => set({ threads, threadsStatus }),
      setThreadsStatus: (threadsStatus) => set({ threadsStatus }),
      upsertThread: (thread) =>
        set((state) => ({
          threads: state.threads.some((t) => t.id === thread.id)
            ? state.threads.map((t) => (t.id === thread.id ? thread : t))
            : [thread, ...state.threads],
        })),
      removeThread: (id) =>
        set((state) => {
          const messages = { ...state.messages };
          delete messages[id];
          return {
            threads: state.threads.filter((thread) => thread.id !== id),
            messages,
            activeThreadId: state.activeThreadId === id ? null : state.activeThreadId,
          };
        }),
      setMessages: (threadId, messages) =>
        set((state) => ({ messages: { ...state.messages, [threadId]: messages } })),
      updateMessages: (threadId, update) =>
        set((state) => ({
          messages: { ...state.messages, [threadId]: update(state.messages[threadId] ?? []) },
        })),
      setModels: (models) => set({ models }),
      setActiveThread: (activeThreadId) => set({ activeThreadId }),
      setModel: (model) => set({ model }),
      setDraft: (draft) => set({ draft }),
      addPendingFile: (file) => set((state) => ({ pendingFiles: [...state.pendingFiles, file] })),
      updatePendingFile: (localId, changes) =>
        set((state) => ({
          pendingFiles: state.pendingFiles.map((file) =>
            file.localId === localId ? { ...file, ...changes } : file,
          ),
        })),
      removePendingFile: (localId) =>
        set((state) => ({
          pendingFiles: state.pendingFiles.filter((file) => file.localId !== localId),
        })),
      clearPendingFiles: () => set({ pendingFiles: [] }),
      setController: (controller) => set({ controller }),
      setSimulations: (simulations) => set({ simulations }),
      toggleSimulations: () => set((state) => ({ simulationsOpen: !state.simulationsOpen })),
      closeSimulations: () => set({ simulationsOpen: false }),
      requestChatView: () => set((state) => ({ chatViewRequests: state.chatViewRequests + 1 })),
    }),
    {
      name: "icarus-ui",
      storage: createJSONStorage(() => localStorage),
      // Rehydrated manually on mount so server and first client render match.
      skipHydration: true,
      partialize: ({ activeThreadId, model, draft }) => ({ activeThreadId, model, draft }),
    },
  ),
);

export function useActiveThread(): Thread | null {
  return useChatStore(
    (state) => state.threads.find((thread) => thread.id === state.activeThreadId) ?? null,
  );
}

export function useIsGenerating(): boolean {
  return useChatStore((state) => state.controller !== null);
}
