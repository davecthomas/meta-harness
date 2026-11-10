import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test, vi } from "vitest";
import { InlineField, type InlineFieldProps } from "./InlineField";
import { theme } from "../theme";

// A field holding its own value, as a page would after a commit is taken.
function Holder(props: { initial: any; onCommit: (v: any) => any } & Omit<InlineFieldProps, "value" | "onCommit">) {
  const { initial, onCommit, ...rest } = props;
  const [value, setValue] = useState(initial);
  return (
    <InlineField
      {...(rest as any)}
      value={value}
      onCommit={async (v: any) => {
        const result = await onCommit(v);
        if (!result) setValue(v);
        return result;
      }}
    />
  );
}

function renderField(props: any) {
  return render(
    <MantineProvider theme={theme}>
      <Holder {...props} />
    </MantineProvider>,
  );
}

test("clicking the pencil turns the value into its input, and Enter commits it", async () => {
  const onCommit = vi.fn();
  renderField({ kind: "text", label: "Name", initial: "old", onCommit });

  fireEvent.click(screen.getByRole("button", { name: "Edit Name" }));
  const input = screen.getByLabelText("Name");
  fireEvent.change(input, { target: { value: "new" } });
  fireEvent.keyDown(input, { key: "Enter" });

  await waitFor(() => expect(onCommit).toHaveBeenCalledWith("new"));
  expect(await screen.findByText("new")).toBeTruthy();
  expect(screen.queryByLabelText("Name")).toBeNull();
});

test("clicking the value opens it too, Escape cancels, and leaving the field commits", async () => {
  const onCommit = vi.fn();
  renderField({ kind: "text", label: "Name", initial: "old", onCommit });

  fireEvent.click(screen.getByText("old"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "dropped" } });
  fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Escape" });
  expect(screen.getByText("old")).toBeTruthy();
  expect(onCommit).not.toHaveBeenCalled();

  fireEvent.click(screen.getByText("old"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "kept" } });
  fireEvent.blur(screen.getByLabelText("Name"));
  await waitFor(() => expect(onCommit).toHaveBeenCalledWith("kept"));
});

test("a refused value keeps the input open with the reason under it", async () => {
  renderField({ kind: "text", label: "Name", initial: "old", onCommit: () => "Names are short" });

  fireEvent.click(screen.getByRole("button", { name: "Edit Name" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "a very long name" } });
  fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Enter" });

  expect(await screen.findByText("Names are short")).toBeTruthy();
  expect(screen.getByLabelText("Name")).toBeTruthy();
});

test("a multi-line field commits with Ctrl+Enter, not Enter", async () => {
  const onCommit = vi.fn();
  renderField({ kind: "textarea", label: "Description", initial: "one", onCommit });

  fireEvent.click(screen.getByRole("button", { name: "Edit Description" }));
  const area = screen.getByLabelText("Description");
  fireEvent.change(area, { target: { value: "one\ntwo" } });
  fireEvent.keyDown(area, { key: "Enter" });
  expect(onCommit).not.toHaveBeenCalled();
  fireEvent.keyDown(area, { key: "Enter", ctrlKey: true });
  await waitFor(() => expect(onCommit).toHaveBeenCalledWith("one\ntwo"));
});

test("a number out of its range is refused before it is committed", async () => {
  const onCommit = vi.fn();
  renderField({ kind: "number", label: "Parallel", initial: 4, min: 1, max: 8, onCommit });

  fireEvent.click(screen.getByRole("button", { name: "Edit Parallel" }));
  fireEvent.change(screen.getByLabelText("Parallel"), { target: { value: "12" } });
  fireEvent.keyDown(screen.getByLabelText("Parallel"), { key: "Enter" });
  expect(await screen.findByText("At most 8")).toBeTruthy();
  expect(onCommit).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("Parallel"), { target: { value: "6" } });
  fireEvent.keyDown(screen.getByLabelText("Parallel"), { key: "Enter" });
  await waitFor(() => expect(onCommit).toHaveBeenCalledWith(6));
});

test("a tag list adds a tag with Enter and commits with Enter on an empty entry", async () => {
  const onCommit = vi.fn();
  renderField({ kind: "tags", label: "Checks", initial: ["make test"], monospace: true, onCommit });

  fireEvent.click(screen.getByRole("button", { name: "Edit Checks" }));
  const [input] = screen.getAllByLabelText("Checks", { selector: "input" });
  fireEvent.change(input, { target: { value: "make lint" } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.keyDown(input, { key: "Enter" });

  await waitFor(() => expect(onCommit).toHaveBeenCalledWith(["make test", "make lint"]));
  expect(await screen.findByText("make lint")).toBeTruthy();
});

test("a switch commits on change and shows Saved", async () => {
  const onCommit = vi.fn();
  renderField({ kind: "switch", label: "Auto-merge", switchLabel: "Merge without me", initial: false, showSaved: true, onCommit });

  fireEvent.click(screen.getByRole("switch", { name: "Merge without me" }));
  await waitFor(() => expect(onCommit).toHaveBeenCalledWith(true));
  expect(await screen.findByText("Saved")).toBeTruthy();
});

test("a value marked changed says so, and a read-only one has no pencil", () => {
  render(
    <MantineProvider theme={theme}>
      <InlineField kind="text" label="Note" value="x" changed onCommit={() => undefined} />
      <InlineField kind="text" label="Id" value="y" readOnly onCommit={() => undefined} />
    </MantineProvider>,
  );
  expect(screen.getByText("changed")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit Id" })).toBeNull();
});
