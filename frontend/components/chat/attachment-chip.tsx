import { useState } from "react";
import { BoxIcon, CircleAlertIcon, FileTextIcon, LoaderCircleIcon, XIcon } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ModelViewer } from "./model-viewer";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A file on a message or in the composer, with optional upload status and remove button. */
export function AttachmentChip({
  name,
  size,
  fileId,
  status = "ready",
  error,
  onRemove,
}: {
  name: string;
  size: number;
  /** The uploaded file's id: an STL with one can be opened in the 3D viewer. */
  fileId?: string;
  status?: "uploading" | "ready" | "error";
  error?: string;
  onRemove?: () => void;
}) {
  const Icon =
    status === "uploading" ? LoaderCircleIcon : status === "error" ? CircleAlertIcon : FileTextIcon;
  const [viewing, setViewing] = useState(false);
  const viewable =
    status === "ready" && fileId !== undefined && name.toLowerCase().endsWith(".stl");

  return (
    <>
      <div
        title={error ?? name}
        className={cn(
          "flex max-w-60 items-center gap-2 rounded-lg border bg-background py-1.5 pr-1.5 pl-2 text-xs",
          status === "error" && "border-destructive/40 bg-destructive/5",
        )}
      >
        <Icon
          className={cn(
            "size-4 shrink-0 text-muted-foreground",
            status === "uploading" && "animate-spin",
            status === "error" && "text-destructive",
          )}
        />
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-medium">{name}</span>
          <span
            className={cn(
              "truncate text-muted-foreground",
              status === "error" && "text-destructive",
            )}
          >
            {status === "error" ? (error ?? "Upload failed") : formatSize(size)}
          </span>
        </div>
        {viewable && (
          <button
            type="button"
            aria-label={`View ${name} in 3D`}
            className="ml-auto rounded-md p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={() => setViewing(true)}
          >
            <BoxIcon className="size-3.5" />
          </button>
        )}
        {onRemove && (
          <button
            type="button"
            aria-label={`Remove ${name}`}
            className="ml-auto rounded-md p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={onRemove}
          >
            <XIcon className="size-3.5" />
          </button>
        )}
      </div>
      {viewable && (
        <Dialog open={viewing} onOpenChange={setViewing}>
          <DialogContent className="sm:max-w-3xl">
            <DialogTitle className="truncate">{name}</DialogTitle>
            <DialogDescription className="sr-only">A 3D view of {name}.</DialogDescription>
            {viewing && <ModelViewer source={{ type: "stl", url: api.fileOriginalUrl(fileId) }} />}
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}
