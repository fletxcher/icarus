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
import { getModel, MODELS } from "@/lib/chat/models";
import { useChatStore } from "@/stores/chat-store";

export function ModelSelector() {
  const modelId = useChatStore((state) => state.model);
  const setModel = useChatStore((state) => state.setModel);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm">
          {getModel(modelId).label}
          <ChevronDownIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuLabel className="text-xs text-muted-foreground">Models</DropdownMenuLabel>
        {MODELS.map((model) => (
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
