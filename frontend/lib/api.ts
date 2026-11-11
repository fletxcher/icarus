import type {
  Attachment,
  Model,
  Provider,
  StreamEvent,
  Thread,
  ThreadWithMessages,
} from "@/lib/chat/types";

// Deployed on Vercel, the backend service is routed under /api on the same domain, so
// requests are same-origin (empty base). In local development the backend runs on its own
// port. NEXT_PUBLIC_API_URL overrides either, e.g. to point at a backend elsewhere.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

/** How many messages the signed-in user has left of their daily allowance. */
export type MessageAllowance = {
  /** An admin, or no limit set: nothing below applies. */
  unlimited: boolean;
  limit: number;
  used: number;
  remaining: number;
  /** When the oldest counted message stops counting, so another can be sent. */
  resets_at: string | null;
  /** Models of these providers use the person's own key, so they never count. */
  own_key_providers: Provider[];
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null);
  let detail = response.statusText;
  if (typeof body?.detail === "string") detail = body.detail;
  // Validation failures list what was wrong with each field.
  else if (Array.isArray(body?.detail) && typeof body.detail[0]?.msg === "string") {
    const [first] = body.detail;
    const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : null;
    detail = field ? `${field}: ${first.msg}` : first.msg;
  }
  if (response.status === 401 && !response.url.includes("/api/auth/")) onUnauthorized?.();
  return new ApiError(response.status, detail || `Request failed (${response.status})`);
}

let onUnauthorized: (() => void) | null = null;

/** Called when the server says the session is gone (not for the sign-in calls themselves). */
export function setOnUnauthorized(handler: () => void) {
  onUnauthorized = handler;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}/api${path}`, {
    credentials: "include", // the session cookie, also when the backend is on another origin
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw await toApiError(response);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export type CurrentUser = {
  id: string;
  email: string;
  name: string | null;
};

export type ModelProvider = Provider;

export type ModelKeyInfo = {
  provider: ModelProvider;
  last4: string;
  /** Ollama only: the server's address. */
  url: string | null;
  updated_at: string;
};

export type CredentialsOverview = {
  model_keys: ModelKeyInfo[];
  /** Providers the server supplies a key for until the person adds their own. */
  default_providers: ModelProvider[];
  /** False when the server can't store credentials (no CREDENTIALS_KEY). */
  storage_available: boolean;
};

/** One part of the app, as the backend sees it. */
export type StatusCheck = {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
};

export const api = {
  /** Who is signed in; rejects with a 401 ApiError when nobody is. */
  me: () => request<CurrentUser>("/auth/me"),
  /** Which sign-in methods besides a password the server offers. */
  providers: () => request<{ google: boolean }>("/auth/providers"),
  /** Where signing in with Google starts (a full-page navigation, not a fetch). */
  googleLoginUrl: () => `${API_URL}/api/auth/google/login`,
  status: () => request<StatusCheck[]>("/status"),
  credentials: () => request<CredentialsOverview>("/credentials"),
  saveModelKey: (provider: ModelProvider, apiKey: string) =>
    request<ModelKeyInfo>(`/credentials/model-keys/${provider}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  /** Saves the address of the person's Ollama server; its installed models become choices. */
  saveOllama: (baseUrl: string) =>
    request<ModelKeyInfo>("/credentials/ollama", {
      method: "PUT",
      body: JSON.stringify({ base_url: baseUrl }),
    }),
  removeModelKey: (provider: ModelProvider) =>
    request<void>(`/credentials/model-keys/${provider}`, { method: "DELETE" }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  /** Signs in: the backend answers with the session cookie, which the browser then keeps. */
  login: (credentials: { email: string; password: string }) =>
    request<CurrentUser>("/auth/login", {
      method: "POST",
      body: JSON.stringify(credentials),
    }),
  /** Creates an account. It doesn't sign the person in. */
  signup: (account: { name: string; email: string; password: string }) =>
    request<{ id: string; email: string; name: string | null }>("/auth/signup", {
      method: "POST",
      body: JSON.stringify(account),
    }),
  messageAllowance: () => request<MessageAllowance>("/usage/messages"),
  listModels: () => request<Model[]>("/models"),
  listThreads: () => request<Thread[]>("/threads"),
  getThread: (id: string) => request<ThreadWithMessages>(`/threads/${id}`),
  createThread: (title: string) =>
    request<Thread>("/threads", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  updateThread: (id: string, changes: { title?: string; pinned?: boolean }) =>
    request<Thread>(`/threads/${id}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    }),
  deleteThread: (id: string) => request<void>(`/threads/${id}`, { method: "DELETE" }),
  /** Stores a file to send with a message: text, STL, OBJ, STEP or PDF. */
  uploadFile: async (file: File): Promise<Attachment> => {
    const form = new FormData();
    form.append("file", file);
    // No JSON Content-Type here: the browser sets the multipart boundary itself.
    const response = await fetch(`${API_URL}/api/files`, {
      method: "POST",
      body: form,
      credentials: "include",
    });
    if (!response.ok) throw await toApiError(response);
    return response.json();
  },
  /** A designed part saved as a file (the STL it was built as). */
  fileDownloadUrl: (id: string) => `${API_URL}/api/files/${id}/original?download=true`,
  /** A designed part built again in another format (STEP, 3MF, BREP, OBJ, GLB), to download. */
  designExportUrl: (id: string, format: string) =>
    `${API_URL}/api/designs/${id}/export?format=${format}`,
  /** Removes a file that hasn't been sent yet. */
  deleteFile: (id: string) => request<void>(`/files/${id}`, { method: "DELETE" }),
  /** Deletes a message and every later one (before an edit or regenerate). */
  truncateFrom: (threadId: string, messageId: string) =>
    request<void>(`/threads/${threadId}/messages/${messageId}`, {
      method: "DELETE",
    }),
};

/**
 * Sends a user message (omit `content` to regenerate) and yields the reply's events.
 * Aborting `signal` stops generation; the backend saves the reply as stopped.
 */
export async function* streamReply(
  threadId: string,
  body: {
    content?: string;
    model?: string;
    attachments?: string[];
  },
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(`${API_URL}/api/threads/${threadId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
    signal,
    credentials: "include",
  });
  if (!response.ok || !response.body) throw await toApiError(response);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;

    // Events are separated by a blank line; keep any trailing partial event.
    const events = buffer.split(/\r?\n\r?\n/);
    buffer = events.pop() ?? "";
    for (const raw of events) {
      const event = parseEvent(raw);
      if (event) yield event;
    }
  }
}

function parseEvent(raw: string): StreamEvent | null {
  let type = "message";
  const data: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (data.length === 0) return null;
  return { type, ...JSON.parse(data.join("\n")) } as StreamEvent;
}
