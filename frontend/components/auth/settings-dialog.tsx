"use client";

import {
  ActivityIcon,
  CheckIcon,
  GlobeIcon,
  KeyRoundIcon,
  LogOutIcon,
  MonitorIcon,
  MoonIcon,
  SearchIcon,
  ServerIcon,
  SettingsIcon,
  SunIcon,
  UserIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useState } from "react";
import { toast } from "sonner";
import { ModelKeysPanel } from "@/components/auth/model-keys-panel";
import { UsageBody } from "@/components/auth/usage-summary";
import { WorkstationPanel } from "@/components/auth/workstation-panel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { CurrentUser } from "@/lib/api";
import { LANGUAGES, type LanguageCode, languageName } from "@/lib/languages";
import { cn } from "@/lib/utils";
import { useAuth } from "@/stores/auth-store";
import { useChatStore } from "@/stores/chat-store";
import {
  type Motion,
  type TextSize,
  type TranscriptWidth,
  useSettings,
} from "@/stores/settings-store";

export type SettingsSection =
  "general" | "account" | "language" | "model-keys" | "workstation" | "usage";

const SECTIONS: {
  id: SettingsSection;
  label: string;
  icon: typeof SettingsIcon;
}[] = [
  { id: "general", label: "General", icon: SettingsIcon },
  { id: "account", label: "Account", icon: UserIcon },
  { id: "language", label: "Language", icon: GlobeIcon },
  { id: "model-keys", label: "API keys", icon: KeyRoundIcon },
  { id: "workstation", label: "Work station", icon: ServerIcon },
  { id: "usage", label: "Usage", icon: ActivityIcon },
];

/** One setting: its name (and what it does) on the left, its control on the right. */
type Row = {
  id: string;
  label: string;
  description?: string;
  control: React.ReactNode;
};
type Group = { title: string; rows: Row[] };

function OptionSelect<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
  label: string;
  className?: string;
}) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next as T)}>
      <SelectTrigger aria-label={label} className={cn("w-44", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end">
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function useGeneralGroups(): Group[] {
  const { theme, setTheme } = useTheme();
  const { textSize, width, motion, set } = useSettings();
  const { models, model, setModel } = useChatStore();

  return [
    {
      title: "Appearance",
      rows: [
        {
          id: "theme",
          label: "Theme",
          control: (
            <ToggleGroup
              type="single"
              variant="outline"
              value={theme}
              onValueChange={(value) => value && setTheme(value)}
              aria-label="Theme"
            >
              <ToggleGroupItem value="system" aria-label="System">
                <MonitorIcon />
              </ToggleGroupItem>
              <ToggleGroupItem value="light" aria-label="Light">
                <SunIcon />
              </ToggleGroupItem>
              <ToggleGroupItem value="dark" aria-label="Dark">
                <MoonIcon />
              </ToggleGroupItem>
            </ToggleGroup>
          ),
        },
        {
          id: "text-size",
          label: "Transcript text size",
          control: (
            <OptionSelect<TextSize>
              label="Transcript text size"
              value={textSize}
              onChange={(value) => set({ textSize: value })}
              options={[
                { value: "small", label: "Small" },
                { value: "medium", label: "Medium" },
                { value: "large", label: "Large" },
              ]}
            />
          ),
        },
        {
          id: "width",
          label: "Transcript width",
          control: (
            <OptionSelect<TranscriptWidth>
              label="Transcript width"
              value={width}
              onChange={(value) => set({ width: value })}
              options={[
                { value: "narrow", label: "Narrow" },
                { value: "wide", label: "Wide" },
              ]}
            />
          ),
        },
        {
          id: "motion",
          label: "Motion",
          description:
            "Turns off the flow animation on the login and signup pages.",
          control: (
            <OptionSelect<Motion>
              label="Motion"
              value={motion}
              onChange={(value) => set({ motion: value })}
              options={[
                { value: "system", label: "System" },
                { value: "full", label: "Always animate" },
                { value: "reduced", label: "Reduce" },
              ]}
            />
          ),
        },
      ],
    },
    {
      title: "Chat",
      rows: [
        {
          id: "default-model",
          label: "Default model",
          description:
            "Used for new messages until you pick another in the composer.",
          control:
            models.length > 0 ? (
              <OptionSelect
                label="Default model"
                className="w-56"
                value={model ?? models[0].id}
                onChange={setModel}
                options={models.map((option) => ({
                  value: option.id,
                  label: option.label,
                }))}
              />
            ) : (
              <span className="text-sm text-muted-foreground">
                No models available
              </span>
            ),
        },
      ],
    },
  ];
}

function RowList({ rows }: { rows: Row[] }) {
  return (
    <div className="divide-y">
      {rows.map(({ id, label, description, control }) => (
        <div key={id} className="flex items-center justify-between gap-6 py-4">
          <div className="grid gap-0.5">
            <span className="text-sm">{label}</span>
            {description && (
              <span className="text-sm text-muted-foreground">
                {description}
              </span>
            )}
          </div>
          <div className="shrink-0">{control}</div>
        </div>
      ))}
    </div>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-2 mb-1 text-lg font-semibold first:mt-0">{children}</h2>
  );
}

function GeneralPanel({ groups }: { groups: Group[] }) {
  return (
    <>
      {groups.map(({ title, rows }) => (
        <section key={title} className="mb-8">
          <Heading>{title}</Heading>
          <RowList rows={rows} />
        </section>
      ))}
    </>
  );
}

function LanguagePanel() {
  const { language, setLanguage } = useAuth();
  const options: { code: LanguageCode | null; name: string }[] = [
    { code: null, name: languageName(null) },
    ...LANGUAGES,
  ];

  async function choose(code: LanguageCode | null) {
    try {
      await setLanguage(code);
    } catch {
      toast.error(
        "Couldn't save your language. It will apply in this browser only.",
      );
    }
  }

  return (
    <section>
      <Heading>Language</Heading>
      <p className="mb-4 text-sm text-muted-foreground">
        The language Icarus replies in. &ldquo;Automatic&rdquo; answers in
        whatever language you write in.
      </p>
      <div
        role="radiogroup"
        aria-label="Reply language"
        className="grid grid-cols-2 gap-2"
      >
        {options.map(({ code, name }) => {
          const selected = language === code;
          return (
            <Button
              key={code ?? "auto"}
              type="button"
              role="radio"
              aria-checked={selected}
              variant="outline"
              onClick={() => void choose(code)}
              className={cn(
                "justify-between",
                code === null && "col-span-2",
                selected && "border-primary bg-accent",
              )}
            >
              {name}
              {selected && <CheckIcon />}
            </Button>
          );
        })}
      </div>
    </section>
  );
}

function AccountPanel({
  user,
  onLogout,
}: {
  user: CurrentUser | null;
  onLogout: () => void;
}) {
  const rows: Row[] = user
    ? [
        {
          id: "name",
          label: "Name",
          control: <span className="text-sm">{user.name ?? "–"}</span>,
        },
        {
          id: "email",
          label: "Email",
          control: <span className="text-sm">{user.email}</span>,
        },
        {
          id: "logout",
          label: "Log out",
          description: "Ends this browser's session.",
          control: (
            <Button variant="outline" onClick={onLogout}>
              <LogOutIcon />
              Log out
            </Button>
          ),
        },
      ]
    : [
        {
          id: "signed-out",
          label: "You aren't signed in",
          description:
            "Log in to save your language and see your account here.",
          control: (
            <Button asChild variant="outline">
              <a href="/login">Log in</a>
            </Button>
          ),
        },
      ];
  return (
    <section>
      <Heading>Account</Heading>
      <RowList rows={rows} />
    </section>
  );
}

/**
 * Settings: a list of sections on the left and the chosen section's rows on the right. The
 * search box looks through every row's name.
 */
export function SettingsDialog({
  open,
  onOpenChange,
  user,
  section,
  onSectionChange,
  onLogout,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: CurrentUser | null;
  section: SettingsSection;
  onSectionChange: (section: SettingsSection) => void;
  onLogout: () => void;
}) {
  const [query, setQuery] = useState("");
  const general = useGeneralGroups();

  const needle = query.trim().toLowerCase();
  const sectionMatches = needle
    ? SECTIONS.filter(
        ({ id, label }) =>
          id !== "general" && label.toLowerCase().includes(needle),
      )
    : [];
  const matches = needle
    ? general
        .map((group) => ({
          ...group,
          rows: group.rows.filter((row) =>
            `${row.label} ${row.description ?? ""} ${group.title}`
              .toLowerCase()
              .includes(needle),
          ),
        }))
        .filter((group) => group.rows.length > 0)
    : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setQuery("");
        onOpenChange(next);
      }}
    >
      <DialogContent className="flex h-[min(40rem,85svh)] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
        <DialogTitle className="border-b px-5 py-3.5 text-base font-semibold">
          Settings
        </DialogTitle>
        <DialogDescription className="sr-only">
          Preferences, account, keys, work station and usage.
        </DialogDescription>
        <div className="grid min-h-0 flex-1 grid-cols-[13rem_1fr] max-sm:grid-cols-1">
          <nav className="flex flex-col gap-1 overflow-y-auto border-r p-3 max-sm:border-r-0 max-sm:border-b">
            <div className="relative mb-2">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search"
                aria-label="Search settings"
                className="pl-8"
              />
            </div>
            <span className="px-2 py-1 text-xs text-muted-foreground">
              Preferences
            </span>
            {SECTIONS.map(({ id, label, icon: Icon }) => (
              <Button
                key={id}
                variant="ghost"
                aria-current={!matches && section === id ? "page" : undefined}
                onClick={() => {
                  setQuery("");
                  onSectionChange(id);
                }}
                className={cn(
                  "justify-start font-normal",
                  !matches &&
                    section === id &&
                    "bg-accent text-accent-foreground",
                )}
              >
                <Icon />
                {label}
              </Button>
            ))}
          </nav>
          <div className="min-h-0 overflow-y-auto px-6 py-5">
            {matches ? (
              matches.length > 0 || sectionMatches.length > 0 ? (
                <>
                  {sectionMatches.map(({ id, label, icon: Icon }) => (
                    <Button
                      key={id}
                      variant="outline"
                      className="mb-4 w-full justify-start"
                      onClick={() => {
                        setQuery("");
                        onSectionChange(id);
                      }}
                    >
                      <Icon />
                      {label}
                    </Button>
                  ))}
                  <GeneralPanel groups={matches} />
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No settings match “{query}”.
                </p>
              )
            ) : section === "general" ? (
              <GeneralPanel groups={general} />
            ) : section === "account" ? (
              <AccountPanel user={user} onLogout={onLogout} />
            ) : section === "language" ? (
              <LanguagePanel />
            ) : section === "model-keys" ? (
              <section>
                <Heading>API keys</Heading>
                <p className="mb-3 text-sm text-muted-foreground">
                  Use your own API keys for language model providers. Without one,
                  Icarus&apos;s own key is used where it has one.
                </p>
                <ModelKeysPanel />
              </section>
            ) : section === "workstation" ? (
              <section>
                <Heading>Work station</Heading>
                <p className="mb-3 text-sm text-muted-foreground">
                  The machine that runs XFOIL and the CFD simulations for you.
                </p>
                <WorkstationPanel />
              </section>
            ) : (
              <section>
                <Heading>Usage</Heading>
                <p className="mb-3 text-sm text-muted-foreground">
                  CFD simulations run on the work station. Time is approximate.
                </p>
                <UsageBody />
              </section>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
