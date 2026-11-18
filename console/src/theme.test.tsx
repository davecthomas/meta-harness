import { render, screen } from "@testing-library/react";
import { Badge, MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "./theme";

test("a badge never shrinks below its label, so no table column cuts it short", () => {
  render(
    <MantineProvider theme={theme}>
      <Badge data-testid="badge">most expensive</Badge>
    </MantineProvider>,
  );
  expect(screen.getByTestId("badge").style.minWidth).toBe("max-content");
});
