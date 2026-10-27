"use client";

import { useState } from "react";
import { EyeIcon, EyeOffIcon } from "lucide-react";

import { Input } from "@/components/ui/input";

/** A password field with an eye button that shows or hides what was typed. */
export function PasswordInput({
  ...props
}: Omit<React.ComponentProps<typeof Input>, "type">) {
  const [visible, setVisible] = useState(false);
  const Icon = visible ? EyeOffIcon : EyeIcon;
  return (
    <div className="relative">
      <Input
        {...props}
        type={visible ? "text" : "password"}
        className="bg-background pr-9"
      />
      <button
        type="button"
        onClick={() => setVisible((shown) => !shown)}
        aria-label={visible ? "Hide password" : "Show password"}
        aria-pressed={visible}
        className="absolute inset-y-0 right-0 flex w-9 items-center justify-center rounded-r-md text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground"
      >
        <Icon className="size-4" />
      </button>
    </div>
  );
}
