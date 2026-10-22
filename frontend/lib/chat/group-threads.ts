import type { Thread } from "./types";

export type ThreadGroup = {
  label: string;
  threads: Thread[];
};

const DAY_MS = 24 * 60 * 60 * 1000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** Splits threads into pinned + recency buckets, newest first, omitting empty groups. */
export function groupThreads(threads: Thread[], now = new Date()): ThreadGroup[] {
  const today = startOfDay(now);
  const buckets: ThreadGroup[] = [
    { label: "Pinned", threads: [] },
    { label: "Today", threads: [] },
    { label: "Yesterday", threads: [] },
    { label: "Previous 7 days", threads: [] },
    { label: "Previous 30 days", threads: [] },
    { label: "Older", threads: [] },
  ];

  const sorted = [...threads].sort((a, b) => b.updatedAt - a.updatedAt);

  for (const thread of sorted) {
    if (thread.pinnedAt !== null) {
      buckets[0].threads.push(thread);
      continue;
    }
    const age = today - startOfDay(new Date(thread.updatedAt));
    const index = age <= 0 ? 1 : age <= DAY_MS ? 2 : age <= 7 * DAY_MS ? 3 : age <= 30 * DAY_MS ? 4 : 5;
    buckets[index].threads.push(thread);
  }

  buckets[0].threads.sort((a, b) => (b.pinnedAt ?? 0) - (a.pinnedAt ?? 0));

  return buckets.filter((group) => group.threads.length > 0);
}
