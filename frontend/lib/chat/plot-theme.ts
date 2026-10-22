import type { Layout, Template } from "plotly.js";

/**
 * Plot styling, from a validated data-viz palette: categorical colors in a fixed
 * (colorblind-safe) order, a one-hue sequential ramp, a two-pole diverging scale with a
 * neutral midpoint, and recessive chrome. Light and dark each have their own steps.
 */
const THEMES = {
  light: {
    series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    text: "#0b0b0b",
    secondary: "#52514e",
    muted: "#898781",
    grid: "#e1e0d9",
    axis: "#c3c2b7",
    midpoint: "#f0efec",
  },
  dark: {
    series: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
    text: "#f0efec",
    secondary: "#c3c2b7",
    muted: "#898781",
    grid: "#2c2c2a",
    axis: "#383835",
    midpoint: "#383835",
  },
} as const;

// Blue ramp, near-zero first. On the dark surface it runs the other way, so small
// values still recede into the background.
const BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const RED_ARM = ["#e34948", "#ec835a"];
const BLUE_ARM = ["#2a78d6", "#6da7ec"];

type Scale = [number, string][];

function evenly(colors: readonly string[]): Scale {
  return colors.map((color, index) => [index / (colors.length - 1), color]);
}

export type PlotMode = keyof typeof THEMES;

/**
 * A Plotly template: defaults for every axis and trace that the model's own settings
 * override. The model is told not to set colors, so the palette normally applies.
 */
export function plotTemplate(mode: PlotMode, fontFamily: string): Template {
  const theme = THEMES[mode];
  // Plotly only uses the layout's color scales when a trace has autocolorscale on.
  const field = [
    {
      autocolorscale: true,
      colorbar: {
        outlinewidth: 0,
        tickfont: { color: theme.muted },
        title: { font: { color: theme.secondary } },
      },
    },
  ];
  return {
    layout: themeLayout(mode, fontFamily),
    data: {
      scatter: [{ line: { width: 2 }, marker: { size: 8 } }],
      bar: [{ marker: { line: { width: 0 } } }],
      contour: field,
      heatmap: field,
      surface: field,
      histogram2d: field,
      histogram2dcontour: field,
    },
  } as Template;
}

function themeLayout(mode: PlotMode, fontFamily: string): Partial<Layout> {
  const theme = THEMES[mode];
  const sequential = mode === "light" ? BLUE_RAMP : [...BLUE_RAMP].reverse();
  const axis = {
    color: theme.secondary,
    gridcolor: theme.grid,
    linecolor: theme.axis,
    zerolinecolor: theme.axis,
    tickfont: { color: theme.muted },
    title: { font: { color: theme.secondary } },
    automargin: true,
  };
  return {
    font: { family: fontFamily, color: theme.text, size: 13 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    colorway: [...theme.series],
    colorscale: {
      sequential: evenly(sequential),
      sequentialminus: evenly([...sequential].reverse()),
      diverging: evenly([...BLUE_ARM, theme.midpoint, ...[...RED_ARM].reverse()]),
    },
    xaxis: axis,
    yaxis: axis,
    // A row above the plot, clear of the toolbar in the top-right corner.
    legend: {
      font: { color: theme.secondary },
      bgcolor: "rgba(0,0,0,0)",
      orientation: "h",
      x: 0,
      xanchor: "left",
      y: 1.02,
      yanchor: "bottom",
    },
    hoverlabel: { font: { family: fontFamily } },
    hovermode: "closest",
    margin: { t: 40, r: 16, b: 48, l: 56 },
  } as Partial<Layout>;
}
