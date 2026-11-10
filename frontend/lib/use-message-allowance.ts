"use client";

import { useEffect, useState } from "react";
import { api, type MessageAllowance } from "@/lib/api";

const REOPEN_MARGIN_MS = 1000;

/** The signed-in user's message allowance, looked up again whenever `refreshKey` changes and
 * when a message stops counting and a slot opens. Null until it's known. */
export function useMessageAllowance(
  refreshKey: string,
): MessageAllowance | null {
  const [allowance, setAllowance] = useState<MessageAllowance | null>(null);

  useEffect(() => {
    let current = true;
    api.messageAllowance().then(
      (result) => current && setAllowance(result),
      () => undefined,
    );
    return () => {
      current = false;
    };
  }, [refreshKey]);

  useEffect(() => {
    if (!allowance?.resets_at || allowance.remaining > 0) return;
    let current = true;
    const wait =
      new Date(allowance.resets_at).getTime() - Date.now() + REOPEN_MARGIN_MS;
    const timer = setTimeout(
      () =>
        api.messageAllowance().then(
          (result) => current && setAllowance(result),
          () => undefined,
        ),
      Math.max(wait, REOPEN_MARGIN_MS),
    );
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [allowance]);

  return allowance;
}
