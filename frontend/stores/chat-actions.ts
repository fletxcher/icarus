import { toast } from "sonner";
import { ApiError, api, streamReply } from "@/lib/api";
import { applyStreamEvent } from "@/lib/chat/apply-event";
import type { Message } from "@/lib/chat/types";
import { useChatStore } from "./chat-store";

const TITLE_MAX_LENGTH = 60;

const store = () => useChatStore.getState();

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Couldn't reach the server. Is the backend running?";
}

/** Loads models, threads and the remembered chat. Called once on mount. */
export async function bootstrap() {
  await useChatStore.persist.rehydrate();
  try {
    const [models, threads] = await Promise.all([api.listModels(), api.listThreads()]);
    const { model, activeThreadId, setModels, setThreads, setModel, setActiveThread } = store();
    setModels(models);
    if (!models.some((option) => option.id === model) && models[0]) setModel(models[0].id);
    setThreads(threads);
    if (activeThreadId && threads.some((thread) => thread.id === activeThreadId)) {
      await loadMessages(activeThreadId);
    } else {
      setActiveThread(null);
    }
  } catch (error) {
    store().setThreadsStatus("error");
    toast.error(errorMessage(error));
  }
}

async function loadMessages(threadId: string) {
  try {
    const thread = await api.getThread(threadId);
    store().setMessages(threadId, thread.messages);
    store().upsertThread(thread);
  } catch (error) {
    toast.error(errorMessage(error));
  }
}

export function newChat() {
  store().setActiveThread(null);
}

export async function selectThread(threadId: string) {
  store().setActiveThread(threadId);
  if (!store().messages[threadId]) await loadMessages(threadId);
}

export async function renameThread(threadId: string, title: string) {
  try {
    store().upsertThread(await api.updateThread(threadId, { title }));
  } catch (error) {
    toast.error(errorMessage(error));
  }
}

export async function togglePin(threadId: string) {
  const thread = store().threads.find((t) => t.id === threadId);
  if (!thread) return;
  try {
    store().upsertThread(await api.updateThread(threadId, { pinned: thread.pinned_at === null }));
  } catch (error) {
    toast.error(errorMessage(error));
  }
}

export async function deleteThread(threadId: string) {
  try {
    await api.deleteThread(threadId);
    store().removeThread(threadId);
  } catch (error) {
    toast.error(errorMessage(error));
  }
}

/** Streams one assistant reply into the thread, then syncs with what the server saved. */
async function runTurn(threadId: string, content?: string) {
  const { model, setController, updateMessages } = store();
  const controller = new AbortController();
  setController(controller);

  if (content !== undefined) {
    // Shown right away; replaced by the saved copy when the turn ends.
    const optimistic: Message = {
      id: `pending-${crypto.randomUUID()}`,
      thread_id: threadId,
      role: "user",
      content,
      status: "done",
      model: null,
      parts: [{ type: "text", text: content }],
      error: null,
      created_at: new Date().toISOString(),
    };
    updateMessages(threadId, (messages) => [...messages, optimistic]);
  }

  let replyId: string | null = null;
  const updateReply = (update: (message: Message) => Message) =>
    updateMessages(threadId, (messages) =>
      messages.map((message) => (message.id === replyId ? update(message) : message)),
    );

  try {
    for await (const event of streamReply(threadId, { content, model: model ?? undefined }, controller.signal)) {
      if (event.type === "start") {
        replyId = event.message_id;
        const reply: Message = {
          id: replyId,
          thread_id: threadId,
          role: "assistant",
          content: "",
          status: "streaming",
          model,
          parts: [],
          error: null,
          created_at: new Date().toISOString(),
        };
        updateMessages(threadId, (messages) => [...messages, reply]);
      } else {
        updateReply((message) => applyStreamEvent(message, event));
      }
    }
  } catch (error) {
    if (controller.signal.aborted) {
      updateReply(markStopped);
    } else {
      const reason = errorMessage(error);
      updateReply((message) => ({ ...message, status: "error", error: reason }));
      toast.error(reason);
    }
  } finally {
    setController(null);
    await syncThread(threadId, replyId, controller.signal.aborted);
  }
}

/** Marks a reply stopped the way the backend saves it: unfinished tools become errors. */
function markStopped(message: Message): Message {
  return {
    ...message,
    status: "stopped",
    parts: message.parts.map((part) =>
      part.type === "tool" && part.status === "running"
        ? { ...part, status: "error", result: "Interrupted before the tool finished." }
        : part,
    ),
  };
}

/** Replaces local messages with the saved ones (real ids, final statuses). */
async function syncThread(threadId: string, replyId: string | null, stopped: boolean) {
  const local = store().messages[threadId]?.find((message) => message.id === replyId);
  try {
    const thread = await api.getThread(threadId);
    // After a stop the server may not have saved the reply yet; keep what streamed.
    const messages = thread.messages.map((message) =>
      stopped && local && message.id === replyId && message.status === "streaming"
        ? markStopped(local)
        : message,
    );
    store().setMessages(threadId, messages);
    store().upsertThread(thread);
  } catch {
    // Keep the streamed copy; it'll refresh next time the thread opens.
  }
}

export async function sendMessage(text: string) {
  const content = text.trim();
  if (!content || store().controller) return;

  let threadId = store().activeThreadId;
  if (!threadId) {
    try {
      const thread = await api.createThread(content.slice(0, TITLE_MAX_LENGTH));
      store().upsertThread(thread);
      store().setMessages(thread.id, []);
      store().setActiveThread(thread.id);
      threadId = thread.id;
    } catch (error) {
      toast.error(errorMessage(error));
      return;
    }
  }

  store().setDraft("");
  await runTurn(threadId, content);
}

/** Drops a message and everything after it on both sides. */
async function truncateFrom(threadId: string, messageId: string): Promise<boolean> {
  try {
    await api.truncateFrom(threadId, messageId);
  } catch (error) {
    toast.error(errorMessage(error));
    return false;
  }
  store().updateMessages(threadId, (messages) => {
    const index = messages.findIndex((message) => message.id === messageId);
    return index === -1 ? messages : messages.slice(0, index);
  });
  return true;
}

/** Replaces a user message (and everything after it) with an edited version. */
export async function editMessage(threadId: string, messageId: string, text: string) {
  const content = text.trim();
  if (!content || store().controller) return;
  if (await truncateFrom(threadId, messageId)) await runTurn(threadId, content);
}

/** Drops an assistant reply (and everything after it) and generates a new one. */
export async function regenerateReply(threadId: string, messageId: string) {
  if (store().controller) return;
  if (await truncateFrom(threadId, messageId)) await runTurn(threadId);
}

export function stopGeneration() {
  store().controller?.abort();
}
