import { call } from "../../client";

// One setting of a tracker, as its adapter describes it: the console draws its form from these.
export interface TrackerSetting {
  name: string;
  label: string;
  // text; projects: a list of { key, repo }; statuses: a name for each of todo, in_progress, in_review, done.
  kind: "text" | "projects" | "statuses";
  required: boolean;
  help: string;
}

// A tracker the meta-harness supports, from GET /v1/trackers.
export interface Tracker {
  scheme: string;
  title: string;
  description: string;
  settings: TrackerSetting[];
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

export const trackersApi = {
  trackers: () => call<Trackers>("/v1/trackers"),
};
