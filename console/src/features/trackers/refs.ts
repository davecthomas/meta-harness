import { trackersApi, type Trackers } from "./api";

// An issue's ref names its tracker by its first part: github:owner/repo#12, jira:PROJ-12. These say what a ref
// reads as and where its issue is, for any tracker, from what GET /v1/trackers says of each.

let known: Trackers | null = null;
let loading: Promise<Trackers | null> | null = null;

// The supported trackers, read once and kept. Null when they could not be read: links to another tracker's
// issues are then left out, and GitHub's still work.
export function loadTrackers(): Promise<Trackers | null> {
  loading ??= trackersApi.trackers().then(
    (found) => (known = found),
    () => {
      loading = null;
      return null;
    },
  );
  return loading;
}

// Sets what loadTrackers found: for tests, and after the configuration is saved.
export function setTrackers(found: Trackers | null) {
  known = found;
  loading = found ? Promise.resolve(found) : null;
}

export function trackerOf(ref: string): string {
  const at = ref.indexOf(":");
  return at > 0 ? ref.slice(0, at) : "";
}

// A ref as people read it, without its tracker: owner/repo#12, PROJ-12.
export function refLabel(ref: string): string {
  return ref.replace(/^[a-z]+:/, "");
}

// The tracker's name for people: "GitHub Issues", "Jira"; the scheme itself until the trackers are read.
export function trackerTitle(scheme: string): string {
  return known?.trackers.find((t) => t.scheme === scheme)?.title ?? (scheme === "github" ? "GitHub" : scheme);
}

// Where a person reads the issue, or null when the ref's tracker gives no address.
export function issueUrl(ref: string): string | null {
  const github = ref.match(/^github:([\w.-]+\/[\w.-]+)#(\d+)$/);
  if (github) return `https://github.com/${github[1]}/issues/${github[2]}`;
  const prefix = known?.trackers.find((t) => t.scheme === trackerOf(ref))?.issue_url_prefix;
  return prefix ? `${prefix}${encodeURIComponent(refLabel(ref))}` : null;
}
