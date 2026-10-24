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
      "How do I choose between k-epsilon and k-omega SST for an external aerodynamics case?",
      "How do I set up a mesh independence study for a CFD simulation?",
      "How do I estimate the first cell height to hit a target y+ of 1?",
    ],
  },
  {
    label: "Explain",
    icon: LightbulbIcon,
    prompts: [
      "Explain the difference between RANS, LES and DNS.",
      "What is the CFL condition and why does it matter for stability?",
      "Explain how the SIMPLE algorithm couples pressure and velocity.",
    ],
  },
  {
    label: "Write",
    icon: PencilIcon,
    prompts: [
      "Write a Python script that plots a velocity profile and a pressure contour from CFD output.",
      "Draft the methodology section of a report on a turbulent pipe flow simulation.",
      "Write a short summary of boundary layer theory for a new CFD team member.",
    ],
  },
  {
    label: "Advice",
    icon: BookOpenIcon,
    prompts: [
      "My CFD simulation keeps diverging. What should I check first?",
      "What's the best way to decide between a structured and an unstructured mesh?",
      "How can I speed up a transient CFD simulation without losing accuracy?",
    ],
  },
  {
    label: "Analyze",
    icon: ChartColumnIcon,
    prompts: [
      "Compare finite volume, finite element and finite difference methods for CFD.",
      "Analyze the trade-offs between wall functions and resolving the viscous sublayer.",
      "Analyze why the drag coefficient on a cylinder changes with Reynolds number.",
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
