"use client";

import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useChatStore } from "@/stores/chat-store";

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
        <DropdownMenuLabel className="text-xs text-muted-foreground">Models</DropdownMenuLabel>
        {models.map((model) => (
          <DropdownMenuItem key={model.id} onSelect={() => setModel(model.id)}>
            <div className="flex flex-1 flex-col">
              <span className="font-medium">{model.label}</span>
              <span className="text-xs text-muted-foreground">{model.description}</span>
            </div>
            {model.id === modelId && <CheckIcon />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
