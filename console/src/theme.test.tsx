import { render, screen } from "@testing-library/react";
import { Badge, MantineProvider, type MantineTheme } from "@mantine/core";
import { expect, test } from "vitest";
import { cssVariablesResolver, disabledInputOpacity, textColors, theme } from "./theme";

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// The color a browser shows for fg at the given opacity over bg.
function blend(fg: string, bg: string, opacity: number): string {
  const ch = (i: number) => {
    const f = parseInt(fg.slice(i, i + 2), 16);
    const b = parseInt(bg.slice(i, i + 2), 16);
    return Math.round(f * opacity + b * (1 - opacity)).toString(16).padStart(2, "0");
  };
  return "#" + ch(1) + ch(3) + ch(5);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

for (const scheme of ["light", "dark"] as const) {
  test(`${scheme}: secondary, placeholder and disabled text meet 4.5:1 on the page and a card`, () => {
    const c = textColors[scheme];
    const vars = cssVariablesResolver(theme as MantineTheme)[scheme];
    expect(vars["--mantine-color-dimmed"]).toBe(c.dimmed);
    expect(vars["--mantine-color-placeholder"]).toBe(c.placeholder);
    expect(vars["--mantine-color-disabled-color"]).toBe(c.disabled);
    for (const fg of [c.dimmed, c.placeholder, c.disabled]) {
      for (const bg of [c.page, c.card]) expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(c.disabled, c.disabledFill)).toBeGreaterThanOrEqual(4.5);
    // A disabled input is drawn at disabledInputOpacity over the page or card: blend its text and fill.
    expect(theme.components?.Input).toBeDefined();
    for (const bg of [c.page, c.card]) {
      const text = blend(c.disabled, bg, disabledInputOpacity);
      const fill = blend(c.disabledFill, bg, disabledInputOpacity);
      expect(contrast(text, fill)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(text, bg)).toBeGreaterThanOrEqual(4.5);
    }
  });
}

test("a badge never shrinks below its label, so no table column cuts it short", () => {
  render(
    <MantineProvider theme={theme}>
      <Badge data-testid="badge">most expensive</Badge>
    </MantineProvider>,
  );
  expect(screen.getByTestId("badge").style.minWidth).toBe("max-content");
});
