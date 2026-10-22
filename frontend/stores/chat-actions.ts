import { streamCompletion } from "@/lib/chat/mock-stream";
import type { Message } from "@/lib/chat/types";
import { useChatStore } from "./chat-store";

const TITLE_MAX_LENGTH = 60;

function createMessage(role: Message["role"], content: string, model?: string): Message {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    createdAt: Date.now(),
    status: role === "user" ? "done" : "pending",
    model,
  };
}

async function generateReply(threadId: string) {
  const { threads, model, webSearch, addMessage, updateMessage, setController } =
    useChatStore.getState();
  const history = threads[threadId]?.messages ?? [];
  const controller = new AbortController();
  const reply = createMessage("assistant", "", model);

  setController(controller);
  addMessage(threadId, reply);

  let content = "";
  try {
    const stream = streamCompletion({
      messages: history.map(({ role, content }) => ({ role, content })),
      model,
      webSearch,
      signal: controller.signal,
    });
    for await (const token of stream) {
      content += token;
      updateMessage(threadId, reply.id, { content, status: "streaming" });
    }
    updateMessage(threadId, reply.id, {
      status: controller.signal.aborted ? "stopped" : "done",
    });
  } catch {
    updateMessage(threadId, reply.id, { status: "error" });
  } finally {
    setController(null);
  }
}

export async function sendMessage(text: string) {
  const content = text.trim();
  const state = useChatStore.getState();
  if (!content || state.controller) return;

  const threadId =
    state.activeThreadId ?? state.createThread(content.slice(0, TITLE_MAX_LENGTH));
  state.addMessage(threadId, createMessage("user", content));
  state.setDraft("");
  await generateReply(threadId);
}

/** Replaces a user message (and everything after it) with an edited version. */
export async function editMessage(threadId: string, messageId: string, text: string) {
  const content = text.trim();
  const state = useChatStore.getState();
  if (!content || state.controller) return;

  state.truncateFrom(threadId, messageId);
  state.addMessage(threadId, createMessage("user", content));
  await generateReply(threadId);
}

/** Drops an assistant reply (and everything after it) and generates a new one. */
export async function regenerateReply(threadId: string, messageId: string) {
  const state = useChatStore.getState();
  if (state.controller) return;

  state.truncateFrom(threadId, messageId);
  await generateReply(threadId);
}

export function stopGeneration() {
  useChatStore.getState().controller?.abort();
}
