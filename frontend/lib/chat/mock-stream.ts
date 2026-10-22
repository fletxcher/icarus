import { getModel } from "./models";
import type { Message } from "./types";

export type StreamRequest = {
  messages: Pick<Message, "role" | "content">[];
  model: string;
  webSearch: boolean;
  signal: AbortSignal;
};

/**
 * Placeholder until the backend exists: yields a canned markdown reply word by word.
 * Swap this for a fetch to the backend's streaming endpoint with the same signature.
 */
export async function* streamCompletion({
  messages,
  model,
  webSearch,
  signal,
}: StreamRequest): AsyncGenerator<string> {
  const prompt = messages.at(-1)?.content ?? "";
  const reply = [
    `This is a placeholder response from **${getModel(model).label}**${webSearch ? " with web search" : ""}.`,
    "",
    `You asked:\n\n> ${prompt.split("\n").join("\n> ")}`,
    "",
    "Once the backend is connected, real completions will stream in here. Markdown is supported:",
    "",
    "- **Lists** and _emphasis_",
    "- `inline code` and fenced blocks",
    "",
    "```ts",
    'const greeting = "hello from icarus";',
    "```",
  ].join("\n");

  for (const token of reply.split(/(\s+)/)) {
    if (signal.aborted) return;
    await new Promise((resolve) => setTimeout(resolve, 18));
    yield token;
  }
}
