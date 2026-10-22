import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

const RESET_DELAY_MS = 2000;

export function useCopy() {
  const [copied, setCopied] = useState(false);
  const timeout = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => clearTimeout(timeout.current), []);

  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      clearTimeout(timeout.current);
      timeout.current = setTimeout(() => setCopied(false), RESET_DELAY_MS);
    } catch {
      toast.error("Couldn't copy to clipboard");
    }
  }, []);

  return { copied, copy };
}
