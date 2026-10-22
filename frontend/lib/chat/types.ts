export type Role = "user" | "assistant";

export type MessageStatus = "pending" | "streaming" | "done" | "stopped" | "error";

export type Message = {
  id: string;
  role: Role;
  content: string;
  createdAt: number;
  status: MessageStatus;
  model?: string;
};

export type Thread = {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  pinnedAt: number | null;
  messages: Message[];
};
