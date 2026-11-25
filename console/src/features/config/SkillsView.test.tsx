import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import type { SkillEntry } from "../../api";
import { theme } from "../../theme";
import type { Staging } from "./ConfigViews";
import { setIn } from "./staging";
import { SkillsView, sourceLine } from "./SkillsView";

afterEach(() => vi.unstubAllGlobals());

const SLOP: SkillEntry = {
  description: "Evaluate text for AI slop patterns.",
  source: { kind: "shipped" },
  files: { "SKILL.md": "---\nname: no-ai-slop2\ndescription: Evaluate text.\n---\n\nRead it.\n", LICENSE: "MIT License" },
};
const TIDY: SkillEntry = {
  description: "Tidy a file of notes.",
  source: { kind: "git", url: "https://github.com/acme/skills", ref: "main", path: "skills/tidy-notes", commit: "0123456789abcdef" },
  files: { "SKILL.md": "---\nname: tidy-notes\ndescription: Tidy a file of notes.\n---\n\nRead the notes, then tidy them.\n", "scripts/tidy.py": "print('tidy')\n" },
};

function Staged({ profiles }: { profiles: Record<string, { skills?: string[] }> }) {
  const original = { skills: { "no-ai-slop2": SLOP } };
  const [document, setDocument] = useState<Record<string, any>>(original);
  const staging: Staging = { document, original, stage: (path, value) => setDocument(setIn(document, path, value)), reasons: () => [] };
  return <SkillsView staging={staging} profiles={profiles} />;
}

const show = (profiles: Record<string, { skills?: string[] }> = {}) => render(<MantineProvider theme={theme}><Staged profiles={profiles} /></MantineProvider>);

test("a locked skill shows a lock that says why, and has no remove button; another skill keeps its own", () => {
  const original = { skills: { "no-ai-slop2": { ...SLOP, source: { kind: "shipped" as const, locked: true } }, "tidy-notes": TIDY } };
  const staging: Staging = { document: original, original, stage: () => {}, reasons: () => [] };
  render(<MantineProvider theme={theme}><SkillsView staging={staging} profiles={{}} /></MantineProvider>);
  const locked = screen.getByTestId("skill-no-ai-slop2");
  expect(within(locked).getByRole("img", { name: "no-ai-slop2 is locked" })).toBeTruthy();
  expect(within(locked).queryByRole("button", { name: "Remove skill no-ai-slop2" })).toBeNull();
  const open = screen.getByTestId("skill-tidy-notes");
  expect(within(open).queryByRole("img", { name: "tidy-notes is locked" })).toBeNull();
  expect(within(open).getByRole("button", { name: "Remove skill tidy-notes" })).toBeTruthy();
  fireEvent.click(within(locked).getByRole("button", { name: "no-ai-slop2" }));
  expect(screen.getByRole("dialog").textContent).toContain("changes only by a pull request");
  cleanup();
});

test("each skill shows what it is for, where it came from and which profiles use it; one in use cannot be removed", () => {
  show({ "pi-careful": { skills: ["no-ai-slop2"] } });
  const row = screen.getByTestId("skill-no-ai-slop2");
  expect(row.textContent).toContain("Evaluate text for AI slop patterns.");
  expect(row.textContent).toContain("Ships with Muxxy");
  expect(row.textContent).toContain("pi-careful");
  expect((within(row).getByRole("button", { name: "Remove skill no-ai-slop2" }) as HTMLButtonElement).disabled).toBe(true);
  expect(sourceLine(TIDY.source)).toBe("https://github.com/acme/skills / skills/tidy-notes at main (0123456)");
});

const URL = "https://github.com/acme/skills";
const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

// The service the import form asks: the registered repos, what a repository offers at each ref, and the read.
// A list given as a string cannot be read, and answers 422 with it as the reason. A request held answers only
// once release is called.
function serve({ repos = ["acme/skills", "acme/tools"] as string[] | string, source = "listed" as "listed" | string, skills = null as string[] | null, held = (_path: string): boolean => false } = {}) {
  const read: unknown[] = [];
  const listed: string[] = [];
  let release = () => {};
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    if (held(path)) await released;
    if (path === "/v1/config/repos") {
      return typeof repos === "string" ? json({ detail: repos }, 422) : json({ kind: "repos", version: 1, document: { repos: Object.fromEntries(repos.map((r) => [r, {}])) } });
    }
    if (path.startsWith("/v1/config/skills/source")) {
      const params = new URLSearchParams(path.split("?")[1]);
      listed.push(params.toString());
      if (source !== "listed") return json({ detail: source }, 422);
      const ref = params.get("ref") ?? "main";
      const paths = skills ?? (ref === "v2" ? ["skills/plan", "skills/tidy-notes"] : ["skills/tidy-notes"]);
      return json({ branches: ["main", "next"], tags: ["v2"], default_branch: "main", ref, paths });
    }
    read.push(JSON.parse(String(init?.body)));
    return json({ name: "tidy-notes", skill: TIDY });
  });
  return { read, listed, release };
}

function openImport() {
  show();
  fireEvent.click(screen.getByRole("button", { name: "Import a skill" }));
}

// Opens a picked-name field unless it is open, types into it, and returns its input; Enter commits what is typed.
function typeInto(label: string, value: string) {
  if (!screen.queryByRole("combobox", { name: label })) fireEvent.click(screen.getByRole("button", { name: `Edit ${label}` }));
  const input = screen.getByRole("combobox", { name: label });
  fireEvent.focus(input);
  fireEvent.click(input);
  fireEvent.change(input, { target: { value } });
  return input;
}

// Opens a field once its list has loaded (or failed to): until then it has no pencil. Then types into it.
async function pickFrom(label: string, value: string) {
  if (!screen.queryByRole("combobox", { name: label })) fireEvent.click(await screen.findByRole("button", { name: `Edit ${label}` }));
  await waitFor(() => expect(screen.getByRole("combobox", { name: label }).getAttribute("placeholder")).not.toBe("Loading…"));
  return typeInto(label, value);
}

async function commit(label: string, value: string) {
  fireEvent.keyDown(await pickFrom(label, value), { key: "Enter" });
  await waitFor(() => expect(screen.queryByRole("combobox", { name: label })).toBeNull());
}

// The options a field's list offers. jsdom lays nothing out, so the dropdown may be drawn hidden; its options
// are still the ones offered.
function options(label: string) {
  const list = document.getElementById(screen.getByRole("combobox", { name: label }).getAttribute("aria-controls") ?? "");
  return list ? within(list).queryAllByRole("option", { hidden: true }).map((o) => o.textContent) : [];
}

test("the import offers the registered repos, then the repository's branches and tags, then its skill directories at the ref", async () => {
  const { read, listed } = serve();
  openImport();

  await pickFrom("Repository", "https://github.com/acme");
  await waitFor(() => expect(options("Repository")).toEqual([URL, "https://github.com/acme/tools"]));
  await commit("Repository", URL);

  // Typed and cleared: an empty draft offers the whole list.
  await pickFrom("Branch or tag", "x");
  typeInto("Branch or tag", "");
  await waitFor(() => expect(options("Branch or tag")).toEqual(["main", "next", "v2"]));
  expect(screen.getByRole("combobox", { name: "Branch or tag" }).getAttribute("placeholder")).toBe("Pick a branch or tag");
  await commit("Branch or tag", "v2");

  await pickFrom("Path to the skill", "skills");
  await waitFor(() => expect(options("Path to the skill")).toEqual(["skills/plan", "skills/tidy-notes"]));
  await commit("Path to the skill", "skills/tidy-notes");
  expect(listed).toEqual([`url=${encodeURIComponent(URL)}`, `url=${encodeURIComponent(URL)}&ref=v2`]);

  fireEvent.click(screen.getByRole("button", { name: "Read the skill" }));
  expect(await screen.findByText("scripts/tidy.py")).toBeTruthy();
  expect(read).toEqual([{ url: URL, ref: "v2", path: "skills/tidy-notes" }]);
});

test("a repository, branch or tag, or path the list lacks is refused; an empty branch is the default branch", async () => {
  serve();
  openImport();

  fireEvent.keyDown(await pickFrom("Repository", "https://github.com/other/skills"), { key: "Enter" });
  expect(await screen.findByText(/The registry has no repository named https:\/\/github.com\/other\/skills/)).toBeTruthy();
  await commit("Repository", URL);
  expect(await screen.findByText("the default branch (main)")).toBeTruthy();

  fireEvent.keyDown(await pickFrom("Branch or tag", "nope"), { key: "Enter" });
  expect(await screen.findByText(`${URL} has no branch or tag named nope: pick one from the list, or leave it empty for the default`)).toBeTruthy();
  await commit("Branch or tag", "");

  fireEvent.keyDown(await pickFrom("Path to the skill", "skills/plan"), { key: "Enter" });
  expect(await screen.findByText(new RegExp(`${URL} at main has no skill directory named skills/plan`))).toBeTruthy();
});

test("an empty list that was read refuses every name: no repository when none is registered, no path when none holds a SKILL.md", async () => {
  const readButton = () => screen.getByRole("button", { name: "Read the skill" });
  serve({ repos: [] });
  openImport();
  const repo = await pickFrom("Repository", URL);
  expect(repo.getAttribute("placeholder")).toBe("No repository to pick");
  fireEvent.keyDown(repo, { key: "Enter" });
  expect(await screen.findByText(new RegExp(`The registry has no repository named ${URL}`))).toBeTruthy();
  expect(readButton().hasAttribute("disabled")).toBe(true);
  cleanup();

  serve({ skills: [] });
  openImport();
  await commit("Repository", URL);
  fireEvent.keyDown(await pickFrom("Path to the skill", "skills/tidy-notes"), { key: "Enter" });
  expect(await screen.findByText(new RegExp(`${URL} at main has no skill directory named skills/tidy-notes`))).toBeTruthy();
  // The refused path is not committed: the field stays open on it.
  expect(screen.getByRole("combobox", { name: "Path to the skill" })).toBeTruthy();
});

test("a field takes no name while its list loads, so none the list lacks is committed before it arrives", async () => {
  const readButton = () => screen.getByRole("button", { name: "Read the skill" });
  const repos = serve({ held: (path) => path === "/v1/config/repos" });
  openImport();
  expect(await screen.findByText("Loading…")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit Repository" })).toBeNull();
  expect(readButton().hasAttribute("disabled")).toBe(true);
  repos.release();
  fireEvent.keyDown(await pickFrom("Repository", "https://github.com/other/skills"), { key: "Enter" });
  expect(await screen.findByText(/The registry has no repository named https:\/\/github.com\/other\/skills/)).toBeTruthy();
  cleanup();

  // The branches and tags, then the skill directories at the ref picked, each hold their field until they come.
  const source = serve({ held: (path) => path.startsWith("/v1/config/skills/source") });
  openImport();
  await commit("Repository", URL);
  expect(screen.queryByRole("button", { name: "Edit Branch or tag" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit Path to the skill" })).toBeNull();
  expect(readButton().hasAttribute("disabled")).toBe(true);
  source.release();
  await commit("Branch or tag", "v2");
  await commit("Path to the skill", "skills/plan");
  expect(readButton().hasAttribute("disabled")).toBe(false);
});

test("when a list cannot be read the field says why and still takes a typed value", async () => {
  const { read } = serve({ repos: "the registry is down", source: "could not read https://github.com/acme/skills: Repository not found." });
  openImport();

  expect(await screen.findByText("The registered repositories could not be listed, so type each exactly: the registry is down")).toBeTruthy();
  await commit("Repository", URL);
  expect(await screen.findByText(/The branches and tags could not be listed, so type each exactly: could not read .*Repository not found\./)).toBeTruthy();
  // The same failure leaves the path field without its list too, and it says so.
  expect(await screen.findByText(/The skill directories could not be listed, so type each exactly: could not read .*Repository not found\./)).toBeTruthy();
  expect((await pickFrom("Branch or tag", "release")).getAttribute("placeholder")).toBe("Type its name");
  await commit("Branch or tag", "release");
  expect(await screen.findByText(/The skill directories could not be listed, so type each exactly: could not read .*Repository not found\./)).toBeTruthy();
  expect((await pickFrom("Path to the skill", "skills/tidy-notes")).getAttribute("placeholder")).toBe("Type its name");
  await commit("Path to the skill", "skills/tidy-notes");

  fireEvent.click(screen.getByRole("button", { name: "Read the skill" }));
  expect(await screen.findByText("scripts/tidy.py")).toBeTruthy();
  expect(read).toEqual([{ url: URL, ref: "release", path: "skills/tidy-notes" }]);
});

test("a skill imported from git is read in full before it is added, then staged as new", async () => {
  const { read } = serve();
  openImport();
  await commit("Repository", URL);
  await waitFor(() => expect(screen.getByRole("button", { name: "Edit Path to the skill" })).toBeTruthy());
  await pickFrom("Path to the skill", "skills");
  await waitFor(() => expect(options("Path to the skill")).toEqual(["skills/tidy-notes"]));
  await commit("Path to the skill", "skills/tidy-notes");
  fireEvent.click(screen.getByRole("button", { name: "Read the skill" }));

  // Every file is shown, in full, before anything is added.
  expect(await screen.findByText("scripts/tidy.py")).toBeTruthy();
  expect(screen.getByText(/Read the notes, then tidy them\./)).toBeTruthy();
  expect(read).toEqual([{ url: URL, path: "skills/tidy-notes" }]);
  expect(screen.queryByTestId("skill-tidy-notes")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Add the skill" }));
  await waitFor(() => expect(screen.getByTestId("skill-tidy-notes").textContent).toContain("not saved"));
  // A skill no profile lists is used by no run: the row says so, with the way to change that.
  const unused = screen.getByTestId("skill-tidy-notes");
  expect(unused.textContent).toContain("No profile loads it, so no run uses it.");
  expect(within(unused).getByRole("link", { name: "Add it to a profile" }).getAttribute("href")).toBe("/config?kind=profiles");
  expect(screen.getByTestId("skills-how").textContent).toContain("Nobody types a command for it in a goal or a task.");
});
