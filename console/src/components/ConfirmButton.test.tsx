import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test, vi } from "vitest";
import { ConfirmButton } from "./ConfirmButton";
import { theme } from "../theme";

// An action that cannot be taken back asks first, and says what happened when it fails.

test("the action runs only from the dialog, which says what it does, and a failure keeps it open", async () => {
  const act = vi.fn().mockRejectedValueOnce(new Error("the task is already closed")).mockResolvedValueOnce(undefined);
  render(
    <MantineProvider theme={theme}>
      <ConfirmButton label="Cancel task" title="Cancel this task?" says="The run is stopped." cancel="Keep the task" color="red" onConfirm={act} />
    </MantineProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Cancel task" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(dialog.getByText("The run is stopped.")).toBeTruthy();
  expect(act).not.toHaveBeenCalled();

  fireEvent.click(dialog.getByRole("button", { name: "Cancel task" }));
  const failed = await screen.findByTestId("confirm-failed");
  expect(failed.textContent).toContain("the task is already closed");
  expect(failed.textContent).toContain("Nothing changed");

  fireEvent.click(dialog.getByRole("button", { name: "Cancel task" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(act).toHaveBeenCalledTimes(2);
});

test("the way out leaves everything as it was", async () => {
  const act = vi.fn();
  render(
    <MantineProvider theme={theme}>
      <ConfirmButton label="Merge" title="Merge this pull request?" says="It merges." onConfirm={act} />
    </MantineProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Merge" }));
  fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(act).not.toHaveBeenCalled();
});
