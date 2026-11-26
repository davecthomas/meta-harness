import { createTheme, Input, type CSSVariablesResolver } from "@mantine/core";
import { statusWords } from "./components/status";

// One theme for the whole console, light and dark. Mantine's components carry the layout, spacing
// and states; this sets the palette and type.
const WINDOW_TITLE = {
  title: { fontSize: "var(--mantine-font-size-lg)", fontWeight: 650, lineHeight: 1.3 },
  header: { borderBottom: "1px solid var(--mantine-color-default-border)", marginBottom: "var(--mantine-spacing-md)" },
};

// Secondary text, placeholders and disabled text, per scheme. Mantine's defaults sit under 4.5:1 (WCAG AA) on the
// page and on a card, so the console sets them here once; call sites keep c="dimmed". Disabled text also sits on
// the disabled control's own fill, so it is checked against that too (theme.test.tsx).
export const textColors = {
  light: { dimmed: "#5c636a", placeholder: "#5c636a", disabled: "#5c636a", disabledFill: "#e9ecef", page: "#ffffff", card: "#ffffff" },
  dark: { dimmed: "#a3a3a3", placeholder: "#a3a3a3", disabled: "#adadad", disabledFill: "#2e2e2e", page: "#242424", card: "#2e2e2e" },
};

// The edge of a control a person types in, picks or presses (an input, a default button, a checkbox, a segmented
// control, a tab), per scheme: at least 3:1 on the page and on a card (WCAG 1.4.11), so where a control starts
// and ends is plain. A card's own border stays quieter: it groups, and is not pressed (theme.test.tsx).
export const borderColors = {
  light: { control: "#80868b", card: "#ced4da" },
  dark: { control: "#7a7a7a", card: "#4a4a4a" },
};

// Mantine draws a disabled input at opacity 0.6, which blends its text and fill into the page and drops the
// contrast. The theme sets it back to 1; the disabled colors above already carry the dimming.
export const disabledInputOpacity = 1;

export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {
    "--mantine-color-dimmed": textColors.light.dimmed,
    "--mantine-color-placeholder": textColors.light.placeholder,
    "--mantine-color-disabled-color": textColors.light.disabled,
    "--mantine-color-default-border": borderColors.light.card,
    "--mh-control-border": borderColors.light.control,
  },
  dark: {
    "--mantine-color-dimmed": textColors.dark.dimmed,
    "--mantine-color-placeholder": textColors.dark.placeholder,
    "--mantine-color-disabled-color": textColors.dark.disabled,
    "--mantine-color-default-border": borderColors.dark.card,
    "--mh-control-border": borderColors.dark.control,
  },
});

export const theme = createTheme({
  primaryColor: "indigo",
  defaultRadius: "md",
  fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  fontFamilyMonospace: "ui-monospace, SFMono-Regular, Menlo, monospace",
  // Each level reads as its place in the page: a page's title, then a section's, then a part of a section, each
  // a clear step smaller, and all heavier than body text.
  headings: {
    fontWeight: "650",
    sizes: {
      h1: { fontSize: "2.125rem", lineHeight: "1.25", fontWeight: "700" },
      h2: { fontSize: "1.75rem", lineHeight: "1.3", fontWeight: "700" },
      h3: { fontSize: "1.375rem", lineHeight: "1.35" },
      h4: { fontSize: "1.1875rem", lineHeight: "1.4" },
      h5: { fontSize: "1rem", lineHeight: "1.45" },
      h6: { fontSize: "0.875rem", lineHeight: "1.5" },
    },
  },
  components: {
    Input: Input.extend({ styles: { input: { opacity: disabledInputOpacity, borderColor: "var(--mh-control-border)" } } }),
    // A field's label is the name of what it holds: heavier than the text a person types under it.
    InputWrapper: { styles: { label: { fontWeight: 600, marginBottom: 2 } } },
    // Tabs look like tabs: each one boxed, the chosen one joined to its panel (styles.css finishes the look).
    Tabs: { defaultProps: { variant: "outline" } },
    // A table's header row names its columns, so it reads as a header: heavier, on its own shade.
    Table: { defaultProps: { verticalSpacing: "sm", highlightOnHover: true, withTableBorder: true }, styles: { th: { fontWeight: 650, background: "var(--mantine-color-default-hover)" } } },
    Paper: { defaultProps: { withBorder: true, p: "lg" } },
    // A badge shows its whole label. Mantine lets one shrink and end in "…", which a narrow table column or a
    // full row does to it: "a…" for active, "WARNI…". A table too wide for its frame scrolls there instead.
    Badge: { styles: { root: { minWidth: "max-content" } } },
    // A window's title reads as a title: larger and heavier than its body, over a rule that sets the header off.
    // Set here once, so every dialog and drawer in the console has it.
    Modal: { styles: WINDOW_TITLE },
    Drawer: { styles: WINDOW_TITLE },
  },
});

// A status's color, the same everywhere a task or goal status shows (components/status.ts).
export function statusColor(status: string): string {
  return statusWords(status).color;
}
