// Created by the meta-harness in run t-fc9eba5bd270:run-1 on harness:pi-careful/claude:claude-opus-5-5.

import { render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { Footer, FOOTER_LOGO_HEIGHT } from "./Footer";
import { theme } from "../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

function renderFooter(version?: boolean) {
  return render(
    <MantineProvider theme={theme}>
      <Footer version={version} />
    </MantineProvider>,
  );
}

test("the footer holds the lockup at a size its name reads, the tagline, the version and the GitHub link", async () => {
  vi.stubGlobal("fetch", (url: string) =>
    url === "/v1/version" ? respond({ version: "0.21.1", commit: "4a286e3c0ffee" }) : respond({ detail: "not stubbed" }, 404),
  );

  renderFooter();

  const footer = screen.getByTestId("footer");
  expect(footer.tagName).toBe("FOOTER");
  const image = within(footer).getByRole("img", { name: "Octofusion AI" });
  expect(image.getAttribute("src")).toContain("octofusion-lockup.svg");
  // The footer's logo was a 20-pixel symbol nobody could make out.
  expect(FOOTER_LOGO_HEIGHT).toBeGreaterThanOrEqual(32);
  expect(image.style.getPropertyValue("--image-height") || image.style.height).toContain(`${FOOTER_LOGO_HEIGHT / 16}rem`);
  expect(within(footer).getByText("The Open Meta-Harness for Coding at Scale")).toBeTruthy();
  expect(within(footer).getByRole("link", { name: "GitHub" }).getAttribute("href")).toBe("https://github.com/davecthomas/meta-harness");
  expect((await within(footer).findByTestId("version")).textContent).toBe("v0.21.1 · 4a286e3");
  expect(within(footer).getByRole("link", { name: "4a286e3" }).getAttribute("href")).toBe(
    "https://github.com/davecthomas/meta-harness/commit/4a286e3c0ffee",
  );
});

test("the footer is part of the page, never fixed to the window", () => {
  vi.stubGlobal("fetch", () => respond({ version: "0.21.1", commit: null }));
  renderFooter();
  const footer = screen.getByTestId("footer");
  expect(getComputedStyle(footer).position).not.toBe("fixed");
  expect(getComputedStyle(footer).position).not.toBe("sticky");
});

test("without the version the footer asks for none", () => {
  const fetch = vi.fn(() => respond({ version: "0.21.1", commit: null }));
  vi.stubGlobal("fetch", fetch);

  renderFooter(false);

  expect(within(screen.getByTestId("footer")).getByRole("img", { name: "Octofusion AI" })).toBeTruthy();
  expect(screen.queryByTestId("version")).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});
