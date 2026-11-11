import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { IssuesPicker } from "./IssuesPicker";

afterEach(() => vi.unstubAllGlobals());

function respond(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

const ISSUES = [
  { ref: "github:o/a#1", number: 1, title: "Crash on save", labels: ["bug"], updated_at: "2026-11-09T10:00:00Z", held_by: null },
  { ref: "github:o/a#2", number: 2, title: "Slow load", labels: [], updated_at: "2026-11-09T09:00:00Z", held_by: "g-other" },
  { ref: "github:o/a#3", number: 3, title: "Typo", labels: [], updated_at: "2026-11-09T08:00:00Z", held_by: null },
];

test("the picker lists a repo's open issues, keeps held ones out, and picks the rest a page at a time", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return respond({ issues: ISSUES, total: 3, next_cursor: null });
  });
  const picked: string[][] = [];

  render(
    <MantineProvider theme={theme}>
      <IssuesPicker opened onClose={() => undefined} repos={["o/a"]} action="Add to goal" onPick={(refs) => void picked.push(refs)} />
    </MantineProvider>,
  );

  expect(await screen.findByText("Crash on save")).toBeTruthy();
  expect((screen.getByLabelText("Pick github:o/a#2") as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText("g-other")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Select every issue on this page"));
  expect(screen.getByText("2 selected")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Add to goal" }));

  await waitFor(() => expect(picked).toEqual([["github:o/a#1", "github:o/a#3"]]));
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "crash" } });
  await waitFor(() => expect(asked.some((u) => u.includes("q=crash"))).toBe(true));
});

test("the Label filter suggests the shown repos' labels with their counts, and choosing one filters", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url === "/v1/labels")
      return respond({
        repos: ["o/a", "o/b"],
        labels: [
          { label: "bug", total: 3, held: 0, held_by: [], repos: { "o/a": 2, "o/b": 1 } },
          { label: "docs", total: 1, held: 0, held_by: [], repos: { "o/b": 1 } },
        ],
      });
    return respond({ issues: ISSUES, total: 3, next_cursor: null });
  });

  render(
    <MantineProvider theme={theme}>
      <IssuesPicker opened onClose={() => undefined} repos={["o/a"]} action="Add to goal" onPick={() => undefined} />
    </MantineProvider>,
  );

  const label = await screen.findByRole("combobox", { name: "Label" });
  fireEvent.focus(label);
  fireEvent.change(label, { target: { value: "bu" } });
  // Only o/a is shown, so docs (o/b alone) is not suggested, and bug counts its o/a issues.
  const option = await screen.findByRole("option", { name: /bug/, hidden: true });
  expect(option.textContent).toContain("2 open");
  expect(screen.queryByRole("option", { name: /docs/, hidden: true })).toBeNull();
  fireEvent.click(option);
  await waitFor(() => expect(asked.some((u) => u.startsWith("/v1/repos/o/a/issues") && u.includes("label=bug"))).toBe(true));
});

test("issues the goal's label takes show checked and locked, and are not among the picked", async () => {
  vi.stubGlobal("fetch", () => respond({ issues: ISSUES, total: 3, next_cursor: null, labels: [], repos: [] }));
  const picked: string[][] = [];

  render(
    <MantineProvider theme={theme}>
      <IssuesPicker opened onClose={() => undefined} repos={["o/a"]} viaLabel={["github:o/a#1"]} action="Use these issues" onPick={(refs) => void picked.push(refs)} />
    </MantineProvider>,
  );

  const locked = (await screen.findByRole("checkbox", { name: "github:o/a#1 is in the goal by its label" })) as HTMLInputElement;
  expect(locked.checked && locked.disabled).toBe(true);
  expect(screen.getByText("via label")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select every issue on this page" }));
  fireEvent.click(screen.getByRole("button", { name: "Use these issues" }));
  await waitFor(() => expect(picked).toEqual([["github:o/a#3"]]));
});

test("the Label filter reads its suggestions once the picker opens, and stays plain text when they can't be read", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url === "/v1/labels") return Promise.resolve(new Response(JSON.stringify({ detail: "down" }), { status: 502 }));
    return respond({ issues: ISSUES, total: 3, next_cursor: null });
  });
  const view = (opened: boolean) => (
    <MantineProvider theme={theme}>
      <IssuesPicker opened={opened} onClose={() => undefined} repos={["o/a"]} action="Add to goal" onPick={() => undefined} />
    </MantineProvider>
  );
  const { rerender } = render(view(false));
  await new Promise((r) => setTimeout(r, 20));
  expect(asked).not.toContain("/v1/labels");

  rerender(view(true));
  const label = await screen.findByRole("combobox", { name: "Label" });
  await waitFor(() => expect(asked.filter((u) => u === "/v1/labels")).toHaveLength(1));
  fireEvent.change(label, { target: { value: "bug" } });
  await waitFor(() => expect(asked.some((u) => u.includes("label=bug"))).toBe(true));
  rerender(view(false));
  rerender(view(true));
  expect(asked.filter((u) => u === "/v1/labels")).toHaveLength(1);
});
