import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { LogsPage } from "./LogsPage";

afterEach(() => vi.unstubAllGlobals());

test("the Logs page reads entries with its filters and opens the whole entry", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return Promise.resolve(
      new Response(
        JSON.stringify({
          units: ["mh-worker", "edge"],
          entries: [{ time: "2026-11-09T10:00:00Z", service: "mh-worker", severity: "ERROR", message: "run failed", entry: { insertId: "x1" } }],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  });
  render(
    <MantineProvider theme={theme}>
      <LogsPage />
    </MantineProvider>,
  );

  expect(await screen.findByText("run failed")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "timeout" } });
  await waitFor(() => expect(asked.some((u) => u.includes("q=timeout") && u.includes("minutes=60"))).toBe(true));
  fireEvent.click(screen.getByText("run failed"));
  expect(await screen.findByText(/"insertId": "x1"/)).toBeTruthy();
});
