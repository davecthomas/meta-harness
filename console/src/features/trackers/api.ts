import { call } from "../../client";

// One setting of a tracker, as its adapter describes it: the console draws its form from these.
export interface TrackerSetting {
  name: string;
  label: string;
  // text; statuses: a name for each of todo, in_progress, in_review, done; choice: one of what the tracker
  // lists for it (GET /v1/trackers/{scheme}/choices/{setting}); switch: on or off, "yes" when on. entries: a
  // list the operator adds to, each entry one of what the tracker lists for the setting (Jira's projects) and
  // holding `settings` of its own; `entry` is what one entry is, the name its settings' choices are asked by.
  // entry_of: one of the entries of the setting named in `of` (Jira's default project).
  kind: "text" | "statuses" | "choice" | "switch" | "entries" | "entry_of";
  required: boolean;
  help: string;
  settings?: TrackerSetting[];
  entry?: string;
  of?: string;
}

// A tracker the meta-harness supports, from GET /v1/trackers.
export interface Tracker {
  scheme: string;
  title: string;
  description: string;
  settings: TrackerSetting[];
  // What a goal that uses it says besides its repos, such as the Jira project its issues are in.
  goal_settings: TrackerSetting[];
  // What a new goal starts with for them, from the tracker's settings: Jira's default project.
  goal_defaults?: Record<string, string>;
  // The secrets its sign-in comes from; never in the configuration.
  credentials: string[];
  // Set up: the trackers configuration holds its settings.
  configured: boolean;
  // The meta-harness's own tracker, which is the choice of no tracker: always there, with nothing to set up.
  // A goal on it keeps its tasks in the meta-harness and writes to no outside system.
  built_in?: boolean;
  signed_in: boolean;
  missing_credentials: string[];
  // Whether the planner tells it how an issue's task is going.
  writes_progress: boolean;
  // The start of an issue's address, which the issue's key follows; null when the address depends on the repo.
  issue_url_prefix: string | null;
}

export interface Trackers {
  // The tracker a new goal uses when it names none.
  default: string;
  trackers: Tracker[];
}

// Which issues a goal's are, or a goal to be's: its tracker, and what that tracker asks of a goal. Left out, the
// default tracker's.
export interface IssueSource {
  tracker?: string;
  settings?: Record<string, string>;
}

// A source as the label and issue lists take it in their query.
export function sourceQuery(source?: IssueSource): { tracker?: string; settings?: string } {
  const settings = Object.fromEntries(Object.entries(source?.settings ?? {}).filter(([, v]) => v));
  return { tracker: source?.tracker || undefined, settings: Object.keys(settings).length ? JSON.stringify(settings) : undefined };
}

export const trackersApi = {
  trackers: () => call<Trackers>("/v1/trackers"),
  // What the console offers for one of a tracker's settings: Jira's projects, by name, its issue types, and its
  // status names. group: what a choice belongs to, when the choices come in groups (a status's project).
  // scope: what the setting belongs to, such as the Jira project whose statuses are asked for.
  trackerChoices: (scheme: string, setting: string, scope?: Record<string, string>) => {
    const query = scope && Object.keys(scope).length ? `?${new URLSearchParams(scope)}` : "";
    return call<{ choices: { value: string; label: string }[] }>(`/v1/trackers/${scheme}/choices/${setting}${query}`).then((r) => r.choices);
  },
};
