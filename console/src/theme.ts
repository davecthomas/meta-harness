import { createTheme } from "@mantine/core";

// One theme for the whole console, light and dark. Mantine's components carry the layout, spacing
// and states; this sets the palette and type.
const WINDOW_TITLE = {
  title: { fontSize: "var(--mantine-font-size-lg)", fontWeight: 650, lineHeight: 1.3 },
  header: { borderBottom: "1px solid var(--mantine-color-default-border)", marginBottom: "var(--mantine-spacing-md)" },
};

export const theme = createTheme({
  primaryColor: "indigo",
  defaultRadius: "md",
  fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  fontFamilyMonospace: "ui-monospace, SFMono-Regular, Menlo, monospace",
  headings: { fontWeight: "650" },
  components: {
    Table: { defaultProps: { verticalSpacing: "sm", highlightOnHover: true } },
    Paper: { defaultProps: { withBorder: true, p: "lg" } },
    // A window's title reads as a title: larger and heavier than its body, over a rule that sets the header off.
    // Set here once, so every dialog and drawer in the console has it.
    Modal: { styles: WINDOW_TITLE },
    Drawer: { styles: WINDOW_TITLE },
  },
});

// A status's color, the same everywhere a task or goal status shows.
const STATUS_COLORS: Record<string, string> = {
  active: "teal",
  running: "blue",
  in_review: "violet",
  ready: "teal",
  closed: "gray",
  met: "green",
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
