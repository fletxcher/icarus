import type {
  FeaProgress,
  Simulation,
  SimulationInfo,
  SimulationProgress,
  SimulationSummary,
  SimulationUsage,
} from "@/lib/chat/simulation-types";
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
  preferences: { language: string | null };
};

export type ModelProvider = Provider;

export type ModelKeyInfo = {
  provider: ModelProvider;
  last4: string;
  /** Ollama only: the server's address. */
  url: string | null;
  updated_at: string;
};

export type WorkstationInfo = {
  host: string;
  port: number;
  username: string;
  workdir: string;
  host_key: string;
  fingerprint: string;
  /** The public half of the saved key, for the machine's authorized_keys. */
  public_key: string;
  updated_at: string;
};

export type HostKey = {
  key_type: string;
  fingerprint: string;
  host_key: string;
};

export type WorkstationCheck = {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
};

export type WorkstationReport = {
  workstation: WorkstationInfo;
  checks: WorkstationCheck[];
};

export type CredentialsOverview = {
  model_keys: ModelKeyInfo[];
  workstation: WorkstationInfo | null;
  /** Providers the server supplies a key for until the person adds their own. */
  default_providers: ModelProvider[];
  /** Whether the server has a shared work station used until they add one. */
  default_workstation: boolean;
  /** False when the server can't store credentials (no CREDENTIALS_KEY). */
  storage_available: boolean;
};

export type WorkstationInput = {
  host: string;
  port: number;
  username: string;
  private_key: string;
  passphrase?: string;
  host_key: string;
  workdir?: string;
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
  /** Saves the reply language to the signed-in user's preferences. */
  setLanguage: (language: string | null) =>
    request<CurrentUser>("/auth/me/preferences", {
      method: "PATCH",
      body: JSON.stringify({ language }),
    }),
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
  /** The machine's SSH host key, to confirm it's the right one before connecting. */
  lookUpHostKey: (host: string, port: number) =>
    request<HostKey>("/credentials/workstation/host-key", {
      method: "POST",
      body: JSON.stringify({ host, port }),
    }),
  saveWorkstation: (input: WorkstationInput) =>
    request<WorkstationReport>("/credentials/workstation", {
      method: "PUT",
      body: JSON.stringify(input),
    }),
  checkWorkstation: () =>
    request<WorkstationReport>("/credentials/workstation/check", {
      method: "POST",
    }),
  removeWorkstation: () => request<void>("/credentials/workstation", { method: "DELETE" }),
  simulationUsage: () => request<SimulationUsage>("/simulations/usage"),
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
  listSimulations: () => request<SimulationSummary[]>("/simulations"),
  /** Takes a job off the simulations list, or puts it back. Nothing is deleted. */
  setSimulationHidden: (id: string, hidden: boolean) =>
    request<Simulation>(`/simulations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ hidden }),
    }),
  getSimulation: (id: string) => request<Simulation>(`/simulations/${id}`),
  simulationInfo: (id: string) => request<SimulationInfo>(`/simulations/${id}/info`),
  /** What the run did since the given offsets (0, 0 for everything). */
  /** An FEA job's stage and latest log lines. */
  feaProgress: (id: string) =>
    request<FeaProgress>(`/simulations/${id}/progress?log_offset=0&coeff_offset=0`),
  simulationProgress: (id: string, logOffset: number, coeffOffset: number) =>
    request<SimulationProgress>(
      `/simulations/${id}/progress?log_offset=${logOffset}&coeff_offset=${coeffOffset}`,
    ),
  /** URL of a file a finished job rendered (flow.mp4 / flow.png). */
  simulationFileUrl: (id: string, name: string) => `${API_URL}/api/simulations/${id}/files/${name}`,
  /** URL of an uploaded mesh, CAD file or PDF as it was sent (what the 3D viewer draws). */
  fileOriginalUrl: (id: string) => `${API_URL}/api/files/${id}/original`,
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
    language?: string;
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
