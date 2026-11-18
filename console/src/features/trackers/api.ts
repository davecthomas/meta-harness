import { call } from "../../client";

// One setting of a tracker, as its adapter describes it: the console draws its form from these.
export interface TrackerSetting {
  name: string;
  label: string;
  // text; statuses: a name for each of todo, in_progress, in_review, done; choice: one of what the tracker
  // lists for it (GET /v1/trackers/{scheme}/choices/{setting}); switch: on or off, "yes" when on.
  kind: "text" | "statuses" | "choice" | "switch";
  required: boolean;
  help: string;
}

// A tracker the meta-harness supports, from GET /v1/trackers.
export interface Tracker {
  scheme: string;
  title: string;
  description: string;
  settings: TrackerSetting[];
  // What a goal that uses it says besides its repos, such as the Jira project its issues are in.
  goal_settings: TrackerSetting[];
  // The secrets its sign-in comes from; never in the configuration.
  credentials: string[];
  // Set up: the trackers configuration holds its settings.
  configured: boolean;
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
  // What a goal may choose for one of a tracker's goal settings: Jira's projects, by name.
  trackerChoices: (scheme: string, setting: string) =>
    call<{ choices: { value: string; label: string }[] }>(`/v1/trackers/${scheme}/choices/${setting}`).then((r) => r.choices),
};
