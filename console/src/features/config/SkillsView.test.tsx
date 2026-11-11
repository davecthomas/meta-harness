import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  files: { "SKILL.md": "---\nname: no-ai-slop\ndescription: Evaluate text.\n---\n\nRead it.\n", LICENSE: "MIT License" },
};
const TIDY: SkillEntry = {
  description: "Tidy a file of notes.",
  source: { kind: "git", url: "https://github.com/acme/skills", ref: "main", path: "skills/tidy-notes", commit: "0123456789abcdef" },
  files: { "SKILL.md": "---\nname: tidy-notes\ndescription: Tidy a file of notes.\n---\n\nRead the notes, then tidy them.\n", "scripts/tidy.py": "print('tidy')\n" },
};

function Staged({ profiles }: { profiles: Record<string, { skills?: string[] }> }) {
  const original = { skills: { "no-ai-slop": SLOP } };
  const [document, setDocument] = useState<Record<string, any>>(original);
  const staging: Staging = { document, original, stage: (path, value) => setDocument(setIn(document, path, value)), reasons: () => [] };
  return <SkillsView staging={staging} profiles={profiles} />;
}

const show = (profiles: Record<string, { skills?: string[] }> = {}) => render(<MantineProvider theme={theme}><Staged profiles={profiles} /></MantineProvider>);

test("each skill shows what it is for, where it came from and which profiles use it; one in use cannot be removed", () => {
  show({ "pi-careful": { skills: ["no-ai-slop"] } });
  const row = screen.getByTestId("skill-no-ai-slop");
  expect(row.textContent).toContain("Evaluate text for AI slop patterns.");
  expect(row.textContent).toContain("Ships with the meta-harness");
  expect(row.textContent).toContain("pi-careful");
  expect((within(row).getByRole("button", { name: "Remove skill no-ai-slop" }) as HTMLButtonElement).disabled).toBe(true);
  expect(sourceLine(TIDY.source)).toBe("https://github.com/acme/skills / skills/tidy-notes at main (0123456)");
});

test("a skill imported from git is read in full before it is added, then staged as new", async () => {
  const asked: unknown[] = [];
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
    asked.push(JSON.parse(String(init?.body)));
    return Promise.resolve(new Response(JSON.stringify({ name: "tidy-notes", skill: TIDY }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  show();
  fireEvent.click(screen.getByRole("button", { name: "Import a skill" }));
  fireEvent.change(screen.getByLabelText("Repository"), { target: { value: "https://github.com/acme/skills" } });
  fireEvent.change(screen.getByLabelText("Path to the skill"), { target: { value: "skills/tidy-notes" } });
  fireEvent.click(screen.getByRole("button", { name: "Read the skill" }));

  // Every file is shown, in full, before anything is added.
  expect(await screen.findByText("scripts/tidy.py")).toBeTruthy();
  expect(screen.getByText(/Read the notes, then tidy them\./)).toBeTruthy();
  expect(asked).toEqual([{ url: "https://github.com/acme/skills", path: "skills/tidy-notes" }]);
  expect(screen.queryByTestId("skill-tidy-notes")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Add the skill" }));
  await waitFor(() => expect(screen.getByTestId("skill-tidy-notes").textContent).toContain("new"));
  expect(screen.getByTestId("skill-tidy-notes").textContent).toContain("No profile yet");
});
