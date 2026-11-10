import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../theme";
import { Changes, parsePatch } from "./Changes";

afterEach(() => vi.unstubAllGlobals());

const PATCH = "@@ -1,2 +1,3 @@\n keep\n-old\n+new\n+more";

test("a patch's lines are numbered on each side from its hunk header", () => {
  expect(parsePatch(PATCH).map((l) => [l.kind, l.old, l.new])).toEqual([
    ["hunk", null, null],
    ["ctx", 1, 1],
    ["del", 2, null],
    ["add", null, 2],
    ["add", null, 3],
  ]);
});

test("picking lines with a click and shift-click comments on that range", async () => {
  const posted: any[] = [];
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posted.push(JSON.parse(String(init.body)));
      return Promise.resolve(new Response(JSON.stringify({ url: "u" }), { status: 201, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(
      new Response(
        JSON.stringify({ repo: "o/r", number: 7, head_sha: "abc", state: "open", files: [{ path: "a.py", status: "modified", additions: 2, deletions: 1, patch: PATCH }] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  });
  render(
    <MantineProvider theme={theme}>
      <Notifications />
      <Changes taskId="t-1" />
    </MantineProvider>,
  );

  fireEvent.click(await screen.findByTestId("line-2"));
  fireEvent.click(screen.getByTestId("line-3"), { shiftKey: true });
  fireEvent.change(screen.getByLabelText("Comment"), { target: { value: "Name it better." } });
  fireEvent.click(screen.getByRole("button", { name: "Post comment" }));

  await waitFor(() => expect(posted).toEqual([{ path: "a.py", line: 3, start_line: 2, body: "Name it better." }]));
});
