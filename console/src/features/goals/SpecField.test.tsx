import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { SpecField, type GoalSpec } from "./SpecField";

afterEach(() => vi.unstubAllGlobals());

const respond = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

// The panel as the New goal page holds it: the specification and the goal's parent kept by the page.
function Held(props: { repos?: string[]; tracker?: string; project?: string; parentAsked?: boolean; seen: { spec: GoalSpec | null; parent: string } }) {
  const [spec, setSpec] = useState<GoalSpec | null>(null);
  const [parent, setParent] = useState("");
  props.seen.spec = spec;
  props.seen.parent = parent;
  return (
    <SpecField
      value={spec}
      onChange={setSpec}
      repos={props.repos ?? ["o/a"]}
      tracker={props.tracker ?? "github"}
      project={props.project}
      asksProject={props.tracker === "jira"}
      parentAsked={props.parentAsked}
      parent={parent}
      onParent={setParent}
    />
  );
}

function show(props: Omit<Parameters<typeof Held>[0], "seen">, answers: (url: string) => Promise<Response> | undefined) {
  const asked: string[] = [];
  const seen = { spec: null as GoalSpec | null, parent: "" };
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return answers(url) ?? respond({ detail: "not stubbed" }, 404);
  });
  render(<MantineProvider theme={theme}><Held {...props} seen={seen} /></MantineProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Plan a goal from a spec or an epic" }));
  return { asked, seen };
}

test("a file in the goal's repo is the first way offered: picked from the repo's documents and read", async () => {
  const { asked, seen } = show({}, (url) => {
    if (url === "/v1/repos/o/a/spec-files") return respond({ files: [{ path: "docs/exports-prd.md" }, { path: "README.md" }], complete: true });
    if (url === "/v1/repos/o/a/spec-files/text?path=docs%2Fexports-prd.md") return respond({ path: "docs/exports-prd.md", name: "exports-prd.md", text: "# Exports\n" });
    return undefined;
  });
  expect((screen.getByRole("radio", { name: "A file in the repo" }) as HTMLInputElement).checked).toBe(true);

  fireEvent.click(await screen.findByRole("option", { name: "docs/exports-prd.md", hidden: true }));

  expect((await screen.findByTestId("spec-loaded")).textContent).toContain("docs/exports-prd.md, 10 characters.");
  expect(seen.spec).toEqual({ name: "docs/exports-prd.md", text: "# Exports\n" });
  expect(asked).toContain("/v1/repos/o/a/spec-files");
});

test("when the repo's files cannot be listed, the reason is said and the path is typed", async () => {
  const { seen } = show({}, (url) => {
    if (url === "/v1/repos/o/a/spec-files") return respond({ detail: "file list of o/a could not be read from GitHub. Try again in a minute" }, 502);
    if (url.startsWith("/v1/repos/o/a/spec-files/text?path=docs%2Fa.md")) return respond({ path: "docs/a.md", name: "a.md", text: "Build it." });
    return undefined;
  });

  expect(await screen.findByText(/could not be listed, so type the file's path exactly: .*Try again in a minute/)).toBeTruthy();
  const typed = screen.getByRole("textbox", { name: "File in the repo" });
  fireEvent.change(typed, { target: { value: "docs/a.md" } });
  fireEvent.blur(typed);

  await waitFor(() => expect(seen.spec).toEqual({ name: "docs/a.md", text: "Build it." }));
});

test("with no repository chosen, the panel says to choose one first", () => {
  show({ repos: [] }, () => undefined);
  expect(screen.getByTestId("spec-needs-repo").textContent).toContain("Choose the goal's repository above first");
});

test("an epic is picked in the panel from the tracker's list: it is the specification and the tasks' parent", async () => {
  const { asked, seen } = show({ tracker: "jira", project: "VL", parentAsked: true }, (url) => {
    if (url.startsWith("/v1/trackers/jira/choices/parent")) return respond({ choices: [{ value: "jira:VL-7", label: "VL-7 Data export" }] });
    if (url === "/v1/repos/o/a/spec-files") return respond({ files: [], complete: true });
    return undefined;
  });

  fireEvent.click(screen.getByRole("radio", { name: "An epic or issue" }));
  fireEvent.click(await screen.findByRole("option", { name: "VL-7 Data export", hidden: true }));

  await waitFor(() => expect(seen.spec).toEqual({ ref: "jira:VL-7" }));
  expect(seen.parent).toBe("jira:VL-7");
  expect(asked.some((url) => url.includes("project=VL"))).toBe(true);

  // It can hold the goal's tasks without being what the goal is planned from.
  fireEvent.click(screen.getByRole("checkbox", { name: "Plan the goal from its description" }));
  await waitFor(() => expect(seen.spec).toBeNull());
  expect(seen.parent).toBe("jira:VL-7");

  // Another source lets go of it.
  fireEvent.click(screen.getByRole("radio", { name: "Upload a file" }));
  await waitFor(() => expect(seen.parent).toBe(""));
});
