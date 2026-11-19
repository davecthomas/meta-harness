import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { PickedName, Unreadable } from "./PickedName";
import { theme } from "../theme";

afterEach(cleanup);

function renderField(props: { known: string[] | null; unreadable?: string | null; value?: string; onCommit?: (name: string) => void }) {
  return render(
    <MantineProvider theme={theme}>
      <PickedName
        label="VL: Issue type"
        what="issue type"
        where="VL"
        value={props.value ?? ""}
        empty="The usual"
        known={props.known}
        unreadable={props.unreadable ?? null}
        testId="unknown"
        onCommit={props.onCommit ?? (() => undefined)}
      />
      <Unreadable what="issue types" error={props.unreadable ?? null} />
    </MantineProvider>,
  );
}

function open() {
  fireEvent.click(screen.getByRole("button", { name: "Edit VL: Issue type" }));
  return screen.getByRole("combobox", { name: "VL: Issue type" });
}

test("the list is offered as the operator types", async () => {
  renderField({ known: ["Task", "Bug"] });
  const input = open();
  expect(input.getAttribute("placeholder")).toBe("Pick an issue type");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "Ta" } });
  expect(await screen.findByText("Task")).toBeTruthy();
});

test("a name the list lacks is refused, and one it has is taken in the list's spelling", async () => {
  const onCommit = vi.fn();
  renderField({ known: ["Task", "Bug"], onCommit });
  const input = open();
  fireEvent.change(input, { target: { value: "Taks" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await screen.findByText(/VL has no issue type named Taks/)).toBeTruthy();
  expect(onCommit).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "bug" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(onCommit).toHaveBeenCalledWith("Bug"));
});

test("a saved name the list lacks is marked", () => {
  renderField({ known: ["Task", "Bug"], value: "Story" });
  expect(screen.getByTestId("unknown").textContent).toBe("VL has no issue type named Story");
});

test("a saved name the list has is not marked", () => {
  renderField({ known: ["Task", "Bug"], value: "task" });
  expect(screen.queryByTestId("unknown")).toBeNull();
});

test("a typed value is taken when the list cannot be read, and the reason is shown", async () => {
  const onCommit = vi.fn();
  renderField({ known: null, unreadable: "401 Unauthorized", onCommit });
  expect(screen.getByText(/could not be listed, so type each exactly: 401 Unauthorized/)).toBeTruthy();
  const input = open();
  expect(input.getAttribute("placeholder")).toBe("Type its name");
  fireEvent.change(input, { target: { value: " Story " } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(onCommit).toHaveBeenCalledWith("Story"));
});
