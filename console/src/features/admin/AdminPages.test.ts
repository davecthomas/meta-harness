import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { describeAudit } from "./AdminPages";

const event = (event_type: string, payload: Record<string, unknown>): MhEvent => ({
  position: 1, event_id: "e", event_type, time_utc: "", source: "", actor: "dave", run_id: null, task_id: null, payload,
});

test("audit lines name each change", () => {
  expect(describeAudit(event("kill_switch_changed", { engaged: true, reason: "runaway spend" }))).toBe("Turned the kill switch on: runaway spend");
  expect(describeAudit(event("config_changed", { kind: "tiers", version: 4, origin: "edit", note: "Sonnet first" }))).toBe("saved tiers version 4: Sonnet first");
  expect(describeAudit(event("config_changed", { kind: "profiles", version: 5, origin: "rollback", rolled_back_from: 2 }))).toBe(
    "rolled profiles back to version 2, as version 5",
  );
});
