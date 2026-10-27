"use client";

import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { Fragment } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Model, Provider } from "@/lib/chat/types";
import { useChatStore } from "@/stores/chat-store";

const PROVIDER_LABELS: Record<Provider, string> = {
  mistral: "Mistral",
  openrouter: "OpenRouter",
  ollama: "Ollama (local)",
};

function groupByProvider(models: Model[]): [Provider, Model[]][] {
  const groups = new Map<Provider, Model[]>();
  for (const model of models) {
    groups.set(model.provider, [...(groups.get(model.provider) ?? []), model]);
  }
  return [...groups];
}

export function ModelSelector() {
  const models = useChatStore((state) => state.models);
  const modelId = useChatStore((state) => state.model);
  const setModel = useChatStore((state) => state.setModel);
  const selected = models.find((model) => model.id === modelId);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm" disabled={models.length === 0}>
          {selected?.label ?? "No models"}
          <ChevronDownIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        {groupByProvider(models).map(([provider, group], index) => (
          <Fragment key={provider}>
            {index > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              {PROVIDER_LABELS[provider]}
            </DropdownMenuLabel>
            {group.map((model) => (
              <DropdownMenuItem key={model.id} onSelect={() => setModel(model.id)}>
                <div className="flex flex-1 flex-col">
                  <span className="font-medium">{model.label}</span>
                  <span className="text-xs text-muted-foreground">{model.description}</span>
                </div>
                {model.id === modelId && <CheckIcon />}
              </DropdownMenuItem>
            ))}
          </Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
