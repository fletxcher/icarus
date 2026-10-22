"use client";

import { BookOpenIcon, ChartColumnIcon, CircleHelpIcon, LightbulbIcon, PencilIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat-store";
import { Composer } from "./composer";

const SUGGESTIONS = [
  {
    label: "How to",
    icon: CircleHelpIcon,
    prompts: [
      "How do I set up a personal budget that actually works?",
      "How do I prepare for my first marathon?",
    ],
  },
  {
    label: "Explain",
    icon: LightbulbIcon,
    prompts: [
      "Explain how public-key cryptography works in simple terms.",
      "What is quantum computing and how does it differ from classical computing?",
    ],
  },
  {
    label: "Write",
    icon: PencilIcon,
    prompts: [
      "Write a short story about a chance encounter that changes someone's life.",
      "Draft a friendly follow-up email after a job interview.",
    ],
  },
  {
    label: "Advice",
    icon: BookOpenIcon,
    prompts: [
      "What's the best approach to negotiating a salary increase?",
      "How can I keep a healthy work-life balance while working remotely?",
    ],
  },
  {
    label: "Analyze",
    icon: ChartColumnIcon,
    prompts: [
      "Compare the pros and cons of the major renewable energy sources.",
      "Analyze how remote work has changed city economies.",
    ],
  },
];

function pickRandom<T>(items: T[]): T {
  return items[Math.floor(Math.random() * items.length)];
}

function getGreeting(hour: number): string {
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 18) return "Good afternoon";
  return "Good evening";
}

/** Empty state for a new chat. Only rendered client-side, so reading the clock is safe. */
export function Welcome() {
  const setDraft = useChatStore((state) => state.setDraft);

  const fillSuggestion = (prompts: string[]) => {
    setDraft(pickRandom(prompts));
    document.getElementById("composer")?.focus();
  };

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 pb-[12vh]">
      <div className="flex w-full max-w-3xl flex-col items-center gap-6">
        <h1 className="bg-linear-to-r from-muted-foreground/90 via-muted-foreground/70 to-muted-foreground/40 bg-clip-text text-center text-3xl font-semibold tracking-tight text-transparent sm:text-4xl">
          {getGreeting(new Date().getHours())}
        </h1>
        <Composer />
        <div className="flex flex-wrap justify-center gap-2">
          {SUGGESTIONS.map(({ label, icon: Icon, prompts }) => (
            <Button
              key={label}
              variant="outline"
              size="sm"
              className="rounded-full text-muted-foreground"
              onClick={() => fillSuggestion(prompts)}
            >
              <Icon className="opacity-60" />
              {label}
            </Button>
          ))}
        </div>
      </div>
    </div>
  );
}
