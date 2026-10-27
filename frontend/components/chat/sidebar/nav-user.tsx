"use client";

import {
  ActivityIcon,
  ChevronsUpDownIcon,
  CircleHelpIcon,
  GlobeIcon,
  LogInIcon,
  LogOutIcon,
  SettingsIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { HelpDialog } from "@/components/auth/help-dialog";
import {
  SettingsDialog,
  type SettingsSection,
} from "@/components/auth/settings-dialog";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { languageName } from "@/lib/languages";
import { useAuth } from "@/stores/auth-store";

function initialsOf(text: string): string {
  return (
    text
      .split(/[\s@.]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((word) => word[0].toUpperCase())
      .join("") || "?"
  );
}

export function NavUser() {
  const { isMobile } = useSidebar();
  const router = useRouter();
  const { user, logout, language } = useAuth();
  const [dialog, setDialog] = useState<"settings" | "help" | null>(null);
  const [section, setSection] = useState<SettingsSection>("general");

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key === "," &&
        event.shiftKey &&
        (event.metaKey || event.ctrlKey)
      ) {
        event.preventDefault();
        setSection("general");
        setDialog("settings");
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const name = user ? (user.name ?? user.email) : "Guest";
  const detail = user ? user.email : "Not signed in";

  async function onLogout() {
    try {
      await logout();
      router.push("/login");
    } catch {
      toast.error("Couldn't log out. Try again.");
    }
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground"
            >
              <Avatar className="size-8 rounded-lg">
                <AvatarFallback className="rounded-lg">
                  {user ? initialsOf(name) : "G"}
                </AvatarFallback>
              </Avatar>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">{name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {detail}
                </span>
              </div>
              <ChevronsUpDownIcon className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-56"
            side={isMobile ? "bottom" : "right"}
            align="end"
            sideOffset={4}
          >
            {user && (
              <DropdownMenuLabel className="truncate text-xs font-normal text-muted-foreground">
                {user.email}
              </DropdownMenuLabel>
            )}
            <DropdownMenuItem
              onSelect={() => {
                setSection("general");
                setDialog("settings");
              }}
            >
              <SettingsIcon />
              Settings
              <DropdownMenuShortcut>Ctrl+Shift+,</DropdownMenuShortcut>
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                setSection("usage");
                setDialog("settings");
              }}
            >
              <ActivityIcon />
              Usage
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                setSection("language");
                setDialog("settings");
              }}
            >
              <GlobeIcon />
              Language
              <span className="ml-auto truncate pl-3 text-xs text-muted-foreground">
                {languageName(language)}
              </span>
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setDialog("help")}>
              <CircleHelpIcon />
              Get help
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {user ? (
              <DropdownMenuItem onSelect={onLogout}>
                <LogOutIcon />
                Log out
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem onSelect={() => router.push("/login")}>
                <LogInIcon />
                Log in
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
      <SettingsDialog
        open={dialog === "settings"}
        onOpenChange={(open) => setDialog(open ? "settings" : null)}
        user={user}
        section={section}
        onSectionChange={setSection}
        onLogout={onLogout}
      />
      <HelpDialog
        open={dialog === "help"}
        onOpenChange={(open) => setDialog(open ? "help" : null)}
      />
    </SidebarMenu>
  );
}
