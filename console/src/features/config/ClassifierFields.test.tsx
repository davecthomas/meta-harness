import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { TiersView } from "./TiersView";
import { useClassifierEngines, type ClassifierList } from "./ClassifierFields";
import { theme } from "../../theme";

// The router classifier's engine and model are picked from the list the router library offers.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ENGINES = [
  { engine: "claude", models: ["claude-haiku-5-5", "claude-sonnet-5-5"] },
  { engine: "openai-compatible", models: [] },
  { engine: "openai-decisions", models: ["gpt-6-luna"] },
];

function router(classifier: Record<string, unknown>) {
  return { default_tier: "simple", classifier, tiers: [{ name: "simple", thinking: "low", models: ["harness:pi-lean/claude:claude-sonnet-5-5"] }] };
}

function Tiers({ document, list, staged }: { document: Record<string, any>; list?: ClassifierList; staged: [unknown[], any][] }) {
  const read = useClassifierEngines(list === undefined);
  return (
    <TiersView
      staging={{ document, original: document, stage: (path, value) => staged.push([path, value]), reasons: () => [] }}
      profiles={["pi-lean"]}
      models={["claude:claude-sonnet-5-5"]}
      classifiers={list ?? read}
    />
  );
}

// list: the engines as given, or, when absent, read from GET /v1/config/classifiers.
function show(classifier: Record<string, unknown>, list?: ClassifierList) {
  const staged: [unknown[], any][] = [];
  render(
    <MantineProvider theme={theme}>
      <Tiers document={router(classifier)} list={list} staged={staged} />
    </MantineProvider>,
  );
  return staged;
}

function open(label: string) {
  fireEvent.click(screen.getByRole("button", { name: `Edit ${label}` }));
  return screen.getByRole("combobox", { name: label });
}

test("the engines are offered, the models follow the chosen engine, and a new engine drops a model it lacks", async () => {
  const staged = show({ engine: "claude", model: "claude-haiku-5-5" }, { engines: ENGINES, error: null });

  const model = open("Classifier model");
  expect(model.getAttribute("placeholder")).toBe("Pick a model");
  fireEvent.focus(model);
  fireEvent.change(model, { target: { value: "claude" } });
  expect(await screen.findByRole("option", { name: "claude-sonnet-5-5" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "gpt-6-luna" })).toBeNull();
  fireEvent.keyDown(model, { key: "Escape" });

  const engine = open("Classifier engine");
  expect(engine.getAttribute("placeholder")).toBe("Pick a classifier engine");
  fireEvent.focus(engine);
  fireEvent.change(engine, { target: { value: "openai" } });
  expect(await screen.findByRole("option", { name: "openai-decisions" })).toBeTruthy();
  fireEvent.change(engine, { target: { value: "OpenAI-Decisions" } });
  fireEvent.keyDown(engine, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["classifier"], { engine: "openai-decisions" }]]));
});

test("an empty engine and model keep meaning the defaults", () => {
  show({}, { engines: ENGINES, error: null });
  expect(screen.getByText("the library's default")).toBeTruthy();
  expect(screen.getByText("the engine's default")).toBeTruthy();
});

test("a name the list lacks is refused, and a saved one it lacks is marked", async () => {
  const staged = show({ engine: "claude", model: "gpt-6-luna" }, { engines: ENGINES, error: null });
  expect(screen.getByTestId("classifier-model-unknown").textContent).toBe("Engine claude has no model named gpt-6-luna");
  expect(screen.queryByTestId("classifier-engine-unknown")).toBeNull();

  const engine = open("Classifier engine");
  fireEvent.change(engine, { target: { value: "opnai-decisions" } });
  fireEvent.keyDown(engine, { key: "Enter" });
  expect(await screen.findByText(/The router library has no classifier engine named opnai-decisions/)).toBeTruthy();

  const model = open("Classifier model");
  fireEvent.change(model, { target: { value: "claude-haiku-9" } });
  fireEvent.keyDown(model, { key: "Enter" });
  expect(await screen.findByText(/Engine claude has no model named claude-haiku-9/)).toBeTruthy();
  expect(staged).toEqual([]);
});

test("an engine that lists no models takes a typed model", async () => {
  const staged = show({ engine: "openai-compatible" }, { engines: ENGINES, error: null });
  const model = open("Classifier model");
  expect(model.getAttribute("placeholder")).toBe("Type its name");
  fireEvent.change(model, { target: { value: " qwen3-32b " } });
  fireEvent.keyDown(model, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["classifier"], { engine: "openai-compatible", model: "qwen3-32b" }]]));
});

test("when the list cannot be read the field says why and still takes a typed name", async () => {
  vi.stubGlobal("fetch", (url: string) =>
    Promise.resolve(
      url === "/v1/config/classifiers"
        ? new Response(JSON.stringify({ detail: "Forbidden" }), { status: 403 })
        : new Response("{}", { status: 200 }),
    ),
  );
  const staged = show({});
  expect(await screen.findByText(/classifier engines could not be listed, so type each exactly: Forbidden/)).toBeTruthy();

  const engine = open("Classifier engine");
  expect(engine.getAttribute("placeholder")).toBe("Type its name");
  fireEvent.change(engine, { target: { value: "openai-decisions" } });
  fireEvent.keyDown(engine, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["classifier"], { engine: "openai-decisions" }]]));
});
