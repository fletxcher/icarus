import type {
  Simulation,
  SimulationInfo,
  SimulationProgress,
  SimulationSummary,
} from "@/lib/chat/simulation-types";
import type {
  Attachment,
  Model,
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
  const detail = typeof body?.detail === "string" ? body.detail : response.statusText;
  return new ApiError(response.status, detail || `Request failed (${response.status})`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw await toApiError(response);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export const api = {
  listModels: () => request<Model[]>("/models"),
  listThreads: () => request<Thread[]>("/threads"),
  getThread: (id: string) => request<ThreadWithMessages>(`/threads/${id}`),
  createThread: (title: string) =>
    request<Thread>("/threads", { method: "POST", body: JSON.stringify({ title }) }),
  updateThread: (id: string, changes: { title?: string; pinned?: boolean }) =>
    request<Thread>(`/threads/${id}`, { method: "PATCH", body: JSON.stringify(changes) }),
  deleteThread: (id: string) => request<void>(`/threads/${id}`, { method: "DELETE" }),
  /** Stores a file to send with a message; only text files are accepted for now. */
  uploadFile: async (file: File): Promise<Attachment> => {
    const form = new FormData();
    form.append("file", file);
    // No JSON Content-Type here: the browser sets the multipart boundary itself.
    const response = await fetch(`${API_URL}/api/files`, { method: "POST", body: form });
    if (!response.ok) throw await toApiError(response);
    return response.json();
  },
  listSimulations: () => request<SimulationSummary[]>("/simulations"),
  /** Stops the job if it's running, then deletes its record and its files on the work station. */
  deleteSimulation: (id: string) =>
    request<{ files_removed: boolean }>(`/simulations/${id}`, { method: "DELETE" }),
  getSimulation: (id: string) => request<Simulation>(`/simulations/${id}`),
  simulationInfo: (id: string) => request<SimulationInfo>(`/simulations/${id}/info`),
  /** What the run did since the given offsets (0, 0 for everything). */
  simulationProgress: (id: string, logOffset: number, coeffOffset: number) =>
    request<SimulationProgress>(
      `/simulations/${id}/progress?log_offset=${logOffset}&coeff_offset=${coeffOffset}`,
    ),
  /** URL of a file a finished job rendered (flow.mp4 / flow.png). */
  simulationFileUrl: (id: string, name: string) => `${API_URL}/api/simulations/${id}/files/${name}`,
  /** Removes a file that hasn't been sent yet. */
  deleteFile: (id: string) => request<void>(`/files/${id}`, { method: "DELETE" }),
  /** Deletes a message and every later one (before an edit or regenerate). */
  truncateFrom: (threadId: string, messageId: string) =>
    request<void>(`/threads/${threadId}/messages/${messageId}`, { method: "DELETE" }),
};

/**
 * Sends a user message (omit `content` to regenerate) and yields the reply's events.
 * Aborting `signal` stops generation; the backend saves the reply as stopped.
 */
export async function* streamReply(
  threadId: string,
  body: { content?: string; model?: string; attachments?: string[] },
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(`${API_URL}/api/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
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
