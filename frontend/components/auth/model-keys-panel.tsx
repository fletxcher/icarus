"use client";

import { useEffect, useState } from "react";
import { KeyRoundIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  type CredentialsOverview,
  type ModelKeyInfo,
  type ModelProvider,
} from "@/lib/api";
import { refreshModels } from "@/stores/chat-actions";

const PROVIDERS: {
  id: ModelProvider;
  name: string;
  models: string;
  keysUrl: string;
  /** A server address instead of an API key (Ollama runs the models itself). */
  address?: boolean;
}[] = [
  {
    id: "mistral",
    name: "Mistral",
    models: "Mistral Medium 3.5 and Mistral Small",
    keysUrl: "https://console.mistral.ai/api-keys",
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    models: "OpenRouter Free",
    keysUrl: "https://openrouter.ai/keys",
  },
  {
    id: "ollama",
    name: "Ollama",
    models:
      "Local models installed on an Ollama server (they need tool calling)",
    keysUrl: "https://ollama.com/download",
    address: true,
  },
];

function ProviderRow({
  provider,
  saved,
  shared,
  storageAvailable,
  onChanged,
}: {
  provider: (typeof PROVIDERS)[number];
  saved: ModelKeyInfo | undefined;
  /** The server supplies a key for this provider. */
  shared: boolean;
  storageAvailable: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (provider.address) await api.saveOllama(value.trim());
      else await api.saveModelKey(provider.id, value);
      setValue("");
      setEditing(false);
      toast.success(`${provider.name} key saved`);
      onChanged();
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "Couldn't save the key.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.removeModelKey(provider.id);
      toast.success(`${provider.name} key removed`);
      onChanged();
    } catch (failure) {
      toast.error(
        failure instanceof Error ? failure.message : "Couldn't remove the key.",
      );
    } finally {
      setBusy(false);
    }
  }

  const status = saved
    ? provider.address
      ? `Your server at ${saved.url}`
      : `Your key ending in ${saved.last4}`
    : shared
      ? "Using the key Icarus provides"
      : "No key: these models are unavailable";

  return (
    <div className="grid gap-3 py-4">
      <div className="flex items-center justify-between gap-6">
        <div className="grid gap-0.5">
          <span className="text-sm">{provider.name}</span>
          <span className="text-sm text-muted-foreground">
            {provider.models}
          </span>
          <span
            className="text-sm text-muted-foreground"
            data-testid={`${provider.id}-status`}
          >
            {status}
          </span>
        </div>
        {!editing && (
          <div className="flex shrink-0 gap-2">
            <Button
              variant="outline"
              disabled={!storageAvailable || busy}
              onClick={() => setEditing(true)}
            >
              <KeyRoundIcon />
              {saved ? "Replace" : provider.address ? "Add server" : "Add key"}
            </Button>
            {saved && (
              <Button
                variant="outline"
                size="icon"
                aria-label={`Remove your ${provider.name} ${provider.address ? "server" : "key"}`}
                disabled={busy}
                onClick={() => void remove()}
              >
                <Trash2Icon />
              </Button>
            )}
          </div>
        )}
      </div>
      {editing && (
        <form onSubmit={save} className="grid gap-2">
          {provider.address ? (
            <Input
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="http://localhost:11434"
              aria-label="Ollama server address"
              autoComplete="off"
              spellCheck={false}
              autoFocus
            />
          ) : (
            <PasswordInput
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder={`${provider.name} API key`}
              aria-label={`${provider.name} API key`}
              autoComplete="off"
              autoFocus
            />
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <p className="text-sm text-muted-foreground">
            {provider.address ? "Install it from" : "Make one at"}{" "}
            <a
              href={provider.keysUrl}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-4"
            >
              {provider.keysUrl.replace("https://", "")}
            </a>
            .{" "}
            {provider.address
              ? "The server must be reachable from where Icarus runs."
              : `It's checked with ${provider.name} and stored encrypted.`}
          </p>
          <div className="flex gap-2">
            <Button type="submit" disabled={busy || value.trim().length < 8}>
              {busy
                ? "Checking…"
                : provider.address
                  ? "Save address"
                  : "Save key"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setValue("");
                setError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

/** The person's own model API keys; mount it to load them fresh. */
export function ModelKeysPanel() {
  const [overview, setOverview] = useState<CredentialsOverview | null>(null);
  const [failed, setFailed] = useState(false);

  function load() {
    return api
      .credentials()
      .then(setOverview)
      .catch(() => setFailed(true));
  }

  useEffect(() => {
    let current = true;
    api
      .credentials()
      .then((result) => current && setOverview(result))
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, []);

  async function changed() {
    await load();
    await refreshModels();
  }

  if (failed)
    return (
      <p className="text-sm text-destructive">
        Couldn&apos;t load your keys. Try again.
      </p>
    );
  if (!overview) return <Skeleton className="h-40 rounded-lg" />;

  return (
    <div>
      {!overview.storage_available && (
        <p role="alert" className="mb-2 text-sm text-destructive">
          This server can&apos;t store keys yet: its CREDENTIALS_KEY isn&apos;t
          set.
        </p>
      )}
      <div className="divide-y">
        {PROVIDERS.map((provider) => (
          <ProviderRow
            key={provider.id}
            provider={provider}
            saved={overview.model_keys.find(
              (key) => key.provider === provider.id,
            )}
            shared={overview.default_providers.includes(provider.id)}
            storageAvailable={overview.storage_available}
            onChanged={() => void changed()}
          />
        ))}
      </div>
    </div>
  );
}
