import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import type { ReviewRound, Task } from "../../api";
import { theme } from "../../theme";
import { OperatorReview, TakeOverReview } from "./OperatorReview";

afterEach(() => vi.unstubAllGlobals());

const TASK = { id: "t-1", title: "Fix it", status: "running", pr_url: "https://github.com/o/r/pull/9", review_claimed_by: null } as Task;
const ROUND = {
  round: 1, verdict: "changes", reviewer: "harness:pi-review/x:y", head_sha: "abc", summary: "Two things.", review_url: null, reviewed_at: null, run_id: null,
  findings: [{ n: 1, path: "pyproject.toml", line: 3, body: "Revert the version bump." }, { n: 2, path: "app.py", line: 9, body: "Off by one." }],
  next: { kind: "waiting" },
} as ReviewRound;

function served(answer: Partial<Task>) {
  const asked: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return Promise.resolve(new Response(JSON.stringify({ ...TASK, ...answer }), { status: 202, headers: { "Content-Type": "application/json" } }));
  });
  return asked;
}
const inPage = (ui: React.ReactNode) => render(<MantineProvider theme={theme}>{ui}</MantineProvider>);

test("a running task with a pull request offers the review to take over, and a claimed one does not", async () => {
  const asked = served({ review_claimed_by: "dave" });
  const changed = vi.fn();
  const { rerender } = inPage(<TakeOverReview task={TASK} onChanged={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "Take over this review" }));
  await waitFor(() => expect(changed).toHaveBeenCalled());
  expect(asked).toEqual([{ url: "/v1/tasks/t-1/review/claim", body: null }]);

  rerender(<MantineProvider theme={theme}><TakeOverReview task={{ ...TASK, review_claimed_by: "dave" }} onChanged={changed} /></MantineProvider>);
  expect(screen.queryByRole("button", { name: "Take over this review" })).toBeNull();
});

test("while the agent's review still runs, a claimed review says it will wait, and can be given back", async () => {
  const asked = served({});
  inPage(<OperatorReview task={{ ...TASK, review_claimed_by: "dave" }} rounds={[]} onChanged={() => undefined} />);
  expect(screen.getByTestId("operator-review").textContent).toContain("dave took this review over");
  expect(screen.queryByRole("button", { name: /Submit/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Give the review back to the agent" }));
  await waitFor(() => expect(asked).toEqual([{ url: "/v1/tasks/t-1/review/release", body: null }]));
});

test("the operator keeps the agent's right findings, adds their own, and submits the verdict", async () => {
  const asked = served({});
  const waits = { ...TASK, review_claimed_by: "dave", waiting_on: "operator_review" } as Task;
  inPage(<OperatorReview task={waits} rounds={[ROUND]} onChanged={() => undefined} />);
  expect(screen.getByTestId("operator-review").textContent).toContain("This review waits for dave's verdict");

  fireEvent.click(screen.getByRole("checkbox", { name: "Keep the agent's finding 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Add a finding" }));
  fireEvent.change(screen.getByLabelText("Path of your finding 1"), { target: { value: "app.py" } });
  fireEvent.change(screen.getByLabelText("Your finding 1"), { target: { value: "This loop never ends." } });
  fireEvent.click(screen.getByRole("button", { name: "Submit: changes needed (2)" }));

  await waitFor(() => expect(asked).toHaveLength(1));
  expect(asked[0]).toEqual({
    url: "/v1/tasks/t-1/review/verdict",
    body: { verdict: "changes", keep: [2], findings: [{ body: "This loop never ends.", path: "app.py" }] },
  });
});

test("with every agent finding dropped and none added, the operator submits no change needed", async () => {
  const asked = served({});
  const waits = { ...TASK, review_claimed_by: "dave", waiting_on: "operator_review" } as Task;
  inPage(<OperatorReview task={waits} rounds={[ROUND]} onChanged={() => undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "Submit: no change needed" }));
  await waitFor(() => expect(asked).toHaveLength(1));
  expect(asked[0].body).toEqual({ verdict: "clean", keep: [], findings: [] });
});
