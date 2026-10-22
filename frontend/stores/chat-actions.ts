import { toast } from "sonner";
import { ApiError, api, streamReply } from "@/lib/api";
import { applyStreamEvent } from "@/lib/chat/apply-event";
import type { Attachment, Message } from "@/lib/chat/types";
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

/** Uploads files picked in the composer; each shows its own progress or error. */
export async function addFiles(files: File[]) {
  await Promise.all(
    files.map(async (file) => {
      const localId = crypto.randomUUID();
      store().addPendingFile({ localId, name: file.name, size: file.size, status: "uploading" });
      try {
        const attachment = await api.uploadFile(file);
        store().updatePendingFile(localId, { status: "ready", attachment });
      } catch (error) {
        store().updatePendingFile(localId, { status: "error", error: errorMessage(error) });
      }
    }),
  );
}

/** Takes a file out of the composer, deleting it on the server if it was uploaded. */
export async function removeFile(localId: string) {
  const file = store().pendingFiles.find((pending) => pending.localId === localId);
  store().removePendingFile(localId);
  if (file?.attachment) await api.deleteFile(file.attachment.id).catch(() => undefined);
}

/** Streams one assistant reply into the thread, then syncs with what the server saved. */
async function runTurn(threadId: string, content?: string, attachments: Attachment[] = []) {
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
      attachments,
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
    const body = {
      content,
      model: model ?? undefined,
      attachments: attachments.map((attachment) => attachment.id),
    };
    for await (const event of streamReply(threadId, body, controller.signal)) {
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
          attachments: [],
          error: null,
          created_at: new Date().toISOString(),
        };
        updateMessages(threadId, (messages) => [...messages, reply]);
      } else if (event.type === "title") {
        renameLocally(threadId, event.title);
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

function renameLocally(threadId: string, title: string) {
  const thread = store().threads.find((t) => t.id === threadId);
  if (thread) store().upsertThread({ ...thread, title, auto_title: false });
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
  const { pendingFiles, controller } = store();
  if (!content || controller || pendingFiles.some((file) => file.status === "uploading")) return;
  const attachments = pendingFiles.flatMap((file) => (file.attachment ? [file.attachment] : []));

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
  store().clearPendingFiles();
  await runTurn(threadId, content, attachments);
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
export async function editMessage(
  threadId: string,
  messageId: string,
  text: string,
  attachments: Attachment[],
) {
  const content = text.trim();
  if (!content || store().controller) return;
  if (await truncateFrom(threadId, messageId)) await runTurn(threadId, content, attachments);
}

/** Drops an assistant reply (and everything after it) and generates a new one. */
export async function regenerateReply(threadId: string, messageId: string) {
  if (store().controller) return;
  if (await truncateFrom(threadId, messageId)) await runTurn(threadId);
}

export function stopGeneration() {
  store().controller?.abort();
}
