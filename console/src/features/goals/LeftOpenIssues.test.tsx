import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { LeftOpenIssues } from "./LeftOpenIssues";
import { theme } from "../../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

function show(onClosed = vi.fn()) {
  render(
    <MantineProvider theme={theme}>
      <LeftOpenIssues goalId="g-1" tracker="Jira" onClosed={onClosed} />
    </MantineProvider>,
  );
  return onClosed;
}

test("an ended goal says what it left open and closes it after asking", async () => {
  let left = [
    { ref: "jira:VL-1", title: "Build button" },
    { ref: "jira:VL-2", title: "Build csv" },
  ];
  const posted: string[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/goals/g-1/left-open") return respond({ issues: left });
    if (url === "/v1/goals/g-1/close-left-open" && init?.method === "POST") {
      posted.push(url);
      left = [{ ref: "jira:VL-2", title: "Build csv" }];
      return respond({ closed: ["jira:VL-1"], errors: ["jira:VL-2: its workflow has no status for work that will not be done. Close it in Jira."] });
    }
    return respond({ detail: "not stubbed" }, 404);
  });
  const onClosed = show();

  const notice = await screen.findByTestId("left-open");
  expect(notice.textContent).toContain("2 issues this goal created are still open in Jira");
  expect(notice.textContent).toContain("Build button");

  // One click sends nothing: closing issues in the tracker asks first.
  fireEvent.click(screen.getByRole("button", { name: "Close all 2 as not planned" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(posted).toEqual([]);
  fireEvent.click(dialog.getByRole("button", { name: "Close them" }));

  await waitFor(() => expect(posted).toHaveLength(1));
  expect((await screen.findByTestId("left-open-kept")).textContent).toContain("One could not be closed: jira:VL-2");
  await waitFor(() => expect(screen.getByTestId("left-open").textContent).toContain("1 issue this goal created is still open"));
  expect(onClosed).toHaveBeenCalled();
});

test("a goal that left nothing open shows nothing", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return respond({ issues: [] });
  });
  show();
  await waitFor(() => expect(asked).toEqual(["/v1/goals/g-1/left-open"]));
  expect(screen.queryByTestId("left-open")).toBeNull();
});
