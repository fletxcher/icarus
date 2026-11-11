"use client";

import { BookOpenIcon, ChartColumnIcon, CircleHelpIcon, LightbulbIcon, PencilIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/stores/auth-store";
import { useChatStore } from "@/stores/chat-store";
import { Composer } from "./composer";

const SUGGESTIONS = [
  {
    label: "How to",
    icon: CircleHelpIcon,
    prompts: [
      "How do I choose an airfoil for a low-speed glider?",
      "How do I estimate the Reynolds number for a small wing?",
      "How do I size a bracket to hold a given load?",
    ],
  },
  {
    label: "Explain",
    icon: LightbulbIcon,
    prompts: [
      "Explain why an airfoil stalls and what the lift curve looks like near stall.",
      "What is the difference between laminar and turbulent boundary layers?",
      "Explain what Ncrit does in XFOIL.",
    ],
  },
  {
    label: "Design",
    icon: PencilIcon,
    prompts: [
      "Design an L-shaped mounting bracket, 60 mm by 40 mm, with two M5 holes.",
      "Model a flanged pipe fitting and let me download it as STEP.",
      "Design a simple rectangular heat sink with eight fins.",
    ],
  },
  {
    label: "Advice",
    icon: BookOpenIcon,
    prompts: [
      "Which NACA 4-digit airfoil gives the best L/D at Re = 3e5?",
      "What thickness ratio should I use for a wing that must stay light but stiff?",
      "When can I trust an XFOIL result, and when should I be careful?",
    ],
  },
  {
    label: "Analyze",
    icon: ChartColumnIcon,
    prompts: [
      "Plot the lift polar of a NACA 2412 at Re = 1e6.",
      "Compare NACA 0012 and NACA 4412 at 6° angle of attack.",
      "Analyze how the drag of a NACA 0012 changes with Reynolds number.",
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

/** The first word of the person's name, if they gave one. */
function firstName(name: string | null | undefined): string | null {
  return name?.trim().split(/\s+/)[0] || null;
}

/** Empty state for a new chat. Only rendered client-side, so reading the clock is safe. */
export function Welcome() {
  const setDraft = useChatStore((state) => state.setDraft);
  const name = firstName(useAuth((state) => state.user?.name));

  const fillSuggestion = (prompts: string[]) => {
    setDraft(pickRandom(prompts));
    document.getElementById("composer")?.focus();
  };

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 pb-[12vh]">
      <div className="flex w-full max-w-3xl flex-col items-center gap-6">
        <h1 className="bg-linear-to-r from-muted-foreground/90 via-muted-foreground/70 to-muted-foreground/40 bg-clip-text text-center text-3xl font-semibold tracking-tight text-transparent sm:text-4xl">
          {getGreeting(new Date().getHours())}
          {name && `, ${name}`}
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
