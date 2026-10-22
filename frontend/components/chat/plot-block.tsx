"use client";

import { useEffect, useRef, useState } from "react";
import { ChartLineIcon, ChevronRightIcon, LoaderCircleIcon } from "lucide-react";
import { useTheme } from "next-themes";
import type { Data, Layout } from "plotly.js";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { plotTemplate } from "@/lib/chat/plot-theme";

export type PlotFigure = { data: Data[]; layout?: Partial<Layout> };

const TABLE_MAX_ROWS = 500;

/** Traces simple enough to list as rows (x/y pairs), for the "Show data" table. */
function tableRows(figure: PlotFigure) {
  return figure.data.flatMap((trace) => {
    const { x, y, name } = trace as { x?: unknown; y?: unknown; name?: string };
    if (!Array.isArray(x) || !Array.isArray(y)) return [];
    return x.map((value, index) => ({ series: name ?? "", x: value, y: y[index] }));
  });
}

/** A plot made by the create_plot tool, styled with the app's light/dark theme. */
export function PlotBlock({ figure }: { figure: PlotFigure }) {
  const container = useRef<HTMLDivElement>(null);
  const { resolvedTheme } = useTheme();
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const title = (figure.layout?.title as { text?: string } | undefined)?.text;
  const rows = tableRows(figure);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    let cancelled = false;

    // Plotly is large, so it loads only when a chat actually contains a plot.
    import("plotly.js-dist-min")
      .then(({ default: Plotly }) => {
        if (cancelled) return;
        const fontFamily = getComputedStyle(element).fontFamily;
        const mode = resolvedTheme === "dark" ? "dark" : "light";
        // The title is shown in the card header, in the app's own type.
        const layout = { ...figure.layout, title: undefined, template: plotTemplate(mode, fontFamily) };
        return Plotly.react(element, figure.data, layout, {
          responsive: true,
          displaylogo: false,
          modeBarButtonsToRemove: ["select2d", "lasso2d"],
          toImageButtonOptions: { filename: title ?? "plot" },
        });
      })
      .then(() => !cancelled && setStatus("ready"))
      .catch(() => !cancelled && setStatus("error"));

    return () => {
      cancelled = true;
    };
  }, [figure, resolvedTheme, title]);

  // Free Plotly's resources when the plot leaves the page.
  useEffect(() => {
    const element = container.current;
    return () => {
      if (element) void import("plotly.js-dist-min").then(({ default: Plotly }) => Plotly.purge(element));
    };
  }, []);

  return (
    <figure className="overflow-hidden rounded-lg border">
      <figcaption className="flex items-center gap-2 border-b px-3 py-2 text-sm font-medium">
        <ChartLineIcon className="size-4 text-muted-foreground" />
        {title ?? "Plot"}
      </figcaption>
      <div className="relative">
        <div ref={container} className="h-80 w-full" role="img" aria-label={title ?? "Plot"} />
        {status === "loading" && (
          <div className="absolute inset-0 flex items-center justify-center text-muted-foreground">
            <LoaderCircleIcon className="size-5 animate-spin" />
          </div>
        )}
        {status === "error" && (
          <p className="absolute inset-0 flex items-center justify-center text-sm text-destructive">
            This plot couldn&apos;t be drawn.
          </p>
        )}
      </div>
      {rows.length > 0 && rows.length <= TABLE_MAX_ROWS && (
        <Collapsible className="border-t">
          <CollapsibleTrigger className="group flex w-full items-center gap-1.5 px-3 py-2 text-xs text-muted-foreground hover:text-foreground">
            <ChevronRightIcon className="size-3.5 transition-transform group-data-[state=open]:rotate-90" />
            Show data
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="max-h-64 overflow-auto border-t">
              <table className="w-full text-left text-xs tabular-nums">
                <thead className="sticky top-0 bg-muted text-muted-foreground">
                  <tr>
                    <th className="px-3 py-1.5 font-medium">Series</th>
                    <th className="px-3 py-1.5 font-medium">x</th>
                    <th className="px-3 py-1.5 font-medium">y</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={index} className="border-t">
                      <td className="px-3 py-1">{row.series}</td>
                      <td className="px-3 py-1">{String(row.x)}</td>
                      <td className="px-3 py-1">{String(row.y)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
    </figure>
  );
}
