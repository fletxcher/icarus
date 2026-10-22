// Mirrors the backend's response models (backend/src/api/chats/schemas.py).

export type Role = "user" | "assistant";

export type MessageStatus = "streaming" | "done" | "stopped" | "error";

export type ToolStatus = "running" | "success" | "error";

export type TextPart = { type: "text"; text: string };

export type ReasoningPart = { type: "reasoning"; text: string };

export type ToolPart = {
  type: "tool";
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: ToolStatus;
  result: string | null;
};

/** A reply in the order it happened: reasoning, tool calls and text, possibly interleaved. */
export type MessagePart = TextPart | ReasoningPart | ToolPart;

export type Message = {
  id: string;
  thread_id: string;
  role: Role;
  content: string;
  status: MessageStatus;
  model: string | null;
  parts: MessagePart[];
  /** Why the reply failed, when status is "error". */
  error: string | null;
  created_at: string;
};

export type Thread = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  pinned_at: string | null;
};

export type ThreadWithMessages = Thread & { messages: Message[] };

export type Model = {
  id: string;
  label: string;
  description: string;
};

/** Server-sent events from `POST /api/threads/{id}/messages`. */
export type StreamEvent =
  | { type: "start"; message_id: string }
  | { type: "reasoning"; text: string }
  | { type: "token"; text: string }
  | { type: "tool_call"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; status: "success" | "error"; content: string }
  | { type: "done"; message_id: string }
  | { type: "error"; message: string };
