import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test } from "vitest";
import { theme } from "../theme";
import { ScrollFold } from "./ui";

afterEach(cleanup);

test("a long list sits in its own scrolling box, and folds away and back", () => {
  render(<MantineProvider theme={theme}><ScrollFold what="the 12 tasks"><p>Task one</p></ScrollFold></MantineProvider>);
  const box = screen.getByRole("region", { name: "the 12 tasks" });
  // The box scrolls by itself (styles.css, .mh-scroll), and the keyboard can reach it to scroll.
  expect(box.className).toBe("mh-scroll");
  expect(box.getAttribute("tabindex")).toBe("0");
  const fold = screen.getByRole("button", { name: "Hide the 12 tasks" });
  expect(fold.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(fold);
  expect(screen.getByRole("button", { name: "Show the 12 tasks" }).getAttribute("aria-expanded")).toBe("false");
});
