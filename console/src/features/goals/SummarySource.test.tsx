import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { IssueProgress } from "../../api";
import { theme } from "../../theme";
import { SummarySource } from "./SummarySource";

const show = (more: Partial<IssueProgress>) =>
  render(<MantineProvider theme={theme}><SummarySource issue={{ ref: "github:o/r#1", pr_url: "https://github.com/o/r/pull/9", ...more } as IssueProgress} /></MantineProvider>);

test("a card says whose words its pull request's summary is", () => {
  const { unmount } = show({ summary_source: "run", fix_summaries_written: 0 });
  expect(screen.getByTestId("summary-source").textContent).toBe("summary by the run");
  expect(screen.queryByTestId("fix-summaries-source")).toBeNull();
  unmount();
  show({ summary_source: "recorded" });
  expect(screen.getByTestId("summary-source").textContent).toBe("summary by Muxxy");
});

test("fix rounds Muxxy summarized are marked apart, and the run's opening summary stays the run's", () => {
  const { unmount } = show({ summary_source: "run", fix_summaries_written: 2 });
  expect(screen.getByTestId("summary-source").textContent).toBe("summary by the run");
  expect(screen.getByTestId("fix-summaries-source").textContent).toBe("2 fix rounds by Muxxy");
  unmount();
  show({ summary_source: "files", fix_summaries_written: 1 });
  expect(screen.getByTestId("summary-source").textContent).toBe("summary by Muxxy");
  expect(screen.getByTestId("fix-summaries-source").textContent).toBe("1 fix round by Muxxy");
});

test("a pull request with no summary recorded shows nothing", () => {
  show({ summary_source: null });
  expect(screen.queryByTestId("summary-source")).toBeNull();
  expect(screen.queryByTestId("fix-summaries-source")).toBeNull();
});
