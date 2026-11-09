import { createTheme } from "@mantine/core";

// One theme for the whole console, light and dark. Mantine's components carry the layout, spacing
// and states; this sets the palette and type.
export const theme = createTheme({
  primaryColor: "indigo",
  defaultRadius: "md",
  fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  fontFamilyMonospace: "ui-monospace, SFMono-Regular, Menlo, monospace",
  headings: { fontWeight: "650" },
  components: {
    Table: { defaultProps: { verticalSpacing: "sm", highlightOnHover: true } },
    Paper: { defaultProps: { withBorder: true, p: "lg" } },
  },
});

// A status's color, the same everywhere a task or goal status shows.
const STATUS_COLORS: Record<string, string> = {
  active: "teal",
  running: "blue",
  in_review: "violet",
  ready: "teal",
  closed: "gray",
  met: "teal",
  queued: "yellow",
  blocked: "yellow",
  paused: "yellow",
  not_started: "cyan",
  taken_over: "violet",
  failed: "red",
  canceled: "gray",
  stalled: "orange",
  budget_exhausted: "orange",
  review_unresolved: "orange",
};

export function statusColor(status: string): string {
  return STATUS_COLORS[status] ?? "gray";
}
