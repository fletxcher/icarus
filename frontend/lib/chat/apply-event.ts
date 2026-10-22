import type { Message, MessagePart, StreamEvent } from "./types";

/** Appends streamed text, extending the last part when it's of the same kind. */
function appendText(parts: MessagePart[], type: "text" | "reasoning", text: string) {
  const last = parts.at(-1);
  if (last?.type === type) {
    return [...parts.slice(0, -1), { ...last, text: last.text + text }];
  }
  return [...parts, { type, text }];
}

/** Applies one stream event to the assistant message being generated. */
export function applyStreamEvent(message: Message, event: StreamEvent): Message {
  switch (event.type) {
    case "reasoning":
      return { ...message, parts: appendText(message.parts, "reasoning", event.text) };
    case "token":
      return {
        ...message,
        content: message.content + event.text,
        parts: appendText(message.parts, "text", event.text),
      };
    case "tool_call":
      return {
        ...message,
        parts: [
          ...message.parts,
          { type: "tool", id: event.id, name: event.name, args: event.args, status: "running", result: null },
        ],
      };
    case "tool_result":
      return {
        ...message,
        parts: message.parts.map((part) =>
          part.type === "tool" && part.id === event.id
            ? { ...part, status: event.status, result: event.content }
            : part,
        ),
      };
    case "done":
      return { ...message, status: "done" };
    case "error":
      return { ...message, status: "error", error: event.message };
    case "start":
    case "title":
      return message;
  }
}
