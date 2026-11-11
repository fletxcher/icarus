"use client";

import { useState } from "react";
import { DownloadIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";

/** The formats a designed part can be exported in (backend/src/api/design/service.py). */
export const DESIGN_FORMATS: { id: string; label: string }[] = [
  { id: "step", label: "STEP" },
  { id: "stl", label: "STL" },
  { id: "3mf", label: "3MF" },
  { id: "obj", label: "OBJ" },
  { id: "glb", label: "GLB" },
  { id: "brep", label: "BREP" },
];

export function designDownloadUrl(fileId: string, format: string): string {
  return format === "stl" ? api.fileDownloadUrl(fileId) : api.designExportUrl(fileId, format);
}

/** A part the agent designed: its code, and a download in any format. */
export function DesignCard({ fileId, code }: { fileId: string; code?: string }) {
  const [format, setFormat] = useState("step");
  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={format} onValueChange={setFormat}>
          <SelectTrigger size="sm" aria-label="Download format" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DESIGN_FORMATS.map((option) => (
              <SelectItem key={option.id} value={option.id}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" asChild>
          <a href={designDownloadUrl(fileId, format)} download>
            <DownloadIcon />
            Download
          </a>
        </Button>
        <span className="text-xs text-muted-foreground">
          Other formats are built again from the code, which can take a few seconds.
        </span>
      </div>
      {code && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">build123d code</summary>
          <pre className="mt-1 overflow-x-auto rounded-md bg-muted/60 p-2 font-mono">{code}</pre>
        </details>
      )}
    </div>
  );
}
