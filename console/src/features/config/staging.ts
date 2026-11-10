import type { ConfigKind } from "../../api";

// Changes to a configuration kind are staged on a copy of its document and saved together as one version.
// These are the pieces that work on documents: reading and writing a value at a path, counting what
// differs, placing a refused save's reasons beside the values they are about, and the review's diff.

export type Path = (string | number)[];

export function getIn(document: unknown, path: Path): any {
  let at: any = document;
  for (const key of path) {
    if (at === null || at === undefined) return undefined;
    at = at[key as any];
  }
  return at;
}

// The document with the value at path replaced; undefined removes the key (or the list item).
export function setIn<T>(document: T, path: Path, value: unknown): T {
  if (!path.length) return value as T;
  const [key, ...rest] = path;
  const base: any = document ?? (typeof key === "number" ? [] : {});
  const child = setIn(base[key as any], rest, value);
  if (Array.isArray(base)) {
    const next = [...base];
    if (child === undefined && !rest.length) next.splice(key as number, 1);
    else next[key as number] = child;
    return next as T;
  }
  const next = { ...base };
  if (child === undefined && !rest.length) delete next[key as string];
  else next[key as string] = child;
  return next;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// How many values differ between two documents. A list of plain values (tools, checks, candidates) is one
// value; a list of objects (tiers) and an object are compared by their entries; an added or removed entry is one.
export function countChanges(before: unknown, after: unknown): number {
  if (same(before, after)) return 0;
  if (isObject(before) && isObject(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    let n = 0;
    for (const k of keys) n += countChanges(before[k], after[k]);
    return n;
  }
  const objects = (v: unknown) => Array.isArray(v) && v.some(isObject);
  if (Array.isArray(before) && Array.isArray(after) && (objects(before) || objects(after))) {
    let n = 0;
    for (let i = 0; i < Math.max(before.length, after.length); i++) n += countChanges(before[i], after[i]);
    return n;
  }
  return 1;
}

export function isChanged(before: unknown, after: unknown, path: Path): boolean {
  return !same(getIn(before, path), getIn(after, path));
}

// The fields of a harness profile and of a repo that a refused save's reason can name, after "<name>: ".
const PROFILE_FIELDS = ["tools", "max_turns", "max_minutes", "bash_allow", "resources.cpu", "resources.memory_gb", "image"];
const REPO_FIELDS = ["tracker", "default_checks"];

// The field a reason names after its "<name>: " prefix: "<name>: resources.memory_gb must be ..." is about
// resources.memory_gb, "<name>: unknown tools x" about tools.
function fieldOf(rest: string, fields: string[]): string | null {
  const named = fields.find((f) => rest.startsWith(`${f} `) || rest.startsWith(`${f}:`) || rest.startsWith(`${f}.`));
  if (named) return named;
  if (fields.includes("tools") && rest.startsWith("unknown tools")) return "tools";
  return null;
}

// Where a refused save's reason belongs in a kind's view. A reason about one value targets it:
// "profile:<name>:<field>" (field as in PROFILE_FIELDS, such as "resources.memory_gb"), "repo:<name>:<field>",
// "tier:<name>:thinking", "tier:<name>:candidates", "tier:<name>:candidate:<i>" with ":profile" or ":model"
// when the reason is about that part, or a field ("classifier", "default_tier", "on_exhausted",
// "max_parallel", "reviewers", "max_rounds"). A reason about a tier, profile or repo as a whole targets
// "tier:<name>", "profile:<name>" or "repo:<name>". null: no value in the view is about it, so it shows above the view.
export function reasonTarget(kind: ConfigKind, document: Record<string, any>, reason: string): string | null {
  if (kind === "tiers") {
    const tier = (document.tiers ?? []).find((t: any) => reason.startsWith(`tier ${t.name} `) || reason.startsWith(`tier ${t.name}:`));
    if (tier) {
      const at = `tier:${tier.name}`;
      const rest = reason.slice(`tier ${tier.name}`.length).replace(/^:?\s*/, "");
      if (rest.startsWith("thinking ")) return `${at}:thinking`;
      if (rest.startsWith("has no candidates")) return `${at}:candidates`;
      const index = ((tier.models ?? []) as unknown[]).findIndex((m) => rest.startsWith(`${String(m)} `));
      if (index >= 0) {
        const after = rest.slice(String(tier.models[index]).length + 1);
        if (after.startsWith("names harness profile")) return `${at}:candidate:${index}:profile`;
        if (after.startsWith("names engine")) return `${at}:candidate:${index}:model`;
        return `${at}:candidate:${index}`;
      }
      return at;
    }
    if (reason.startsWith("default_tier")) return "default_tier";
    if (reason.startsWith("classifier")) return "classifier";
    if (reason.startsWith("on_exhausted")) return "on_exhausted";
    return null;
  }
  if (kind === "profiles") {
    const name = Object.keys(document.profiles ?? {}).find((n) => reason.startsWith(`${n}:`));
    if (!name) return null;
    const field = fieldOf(reason.slice(name.length + 1).trimStart(), PROFILE_FIELDS);
    return field ? `profile:${name}:${field}` : `profile:${name}`;
  }
  if (kind === "repos") {
    const name = Object.keys(document.repos ?? {}).find((n) => reason.startsWith(`${n}:`));
    if (!name) return null;
    const field = fieldOf(reason.slice(name.length + 1).trimStart(), REPO_FIELDS);
    return field ? `repo:${name}:${field}` : `repo:${name}`;
  }
  if (reason.startsWith("max_parallel")) return "max_parallel";
  if (reason.startsWith("review.reviewers") || reason.startsWith("reviewer ")) return "reviewers";
  if (reason.startsWith("review.max_rounds")) return "max_rounds";
  return null;
}

// A candidate's parts: harness:<profile>/<engine>:<model>.
export function splitCandidate(candidate: string): { profile: string; model: string } {
  const [profile, ...rest] = candidate.replace(/^harness:/, "").split("/");
  return { profile: profile ?? "", model: rest.join("/") };
}

export function joinCandidate(profile: string, model: string): string {
  return `harness:${profile}/${model}`;
}

export function move<T>(items: T[], from: number, to: number): T[] {
  if (to < 0 || to >= items.length) return items;
  const next = [...items];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

// The lines of `after` that differ from `before`, each marked + or -, for the review before a save.
export function lineDiff(before: string, after: string): { sign: "+" | "-" | " "; text: string }[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const out: { sign: "+" | "-" | " "; text: string }[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ sign: " ", text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) out.push({ sign: "-", text: a[i++] });
    else out.push({ sign: "+", text: b[j++] });
  }
  while (i < n) out.push({ sign: "-", text: a[i++] });
  while (j < m) out.push({ sign: "+", text: b[j++] });
  return out;
}

const isStrings = (v: unknown) => Array.isArray(v) && v.every((x) => typeof x === "string");

// Why a document from Advanced: JSON can't be staged: the containers the kind's view reads are the wrong shape,
// as a tiers field that is a string would be. Empty when the view can show it. The values themselves are left
// to the service, whose refusal Review and save shows beside them.
export function shapeProblems(kind: ConfigKind, document: Record<string, any>): string[] {
  const problems: string[] = [];
  const optional = (value: unknown, check: (v: unknown) => boolean, what: string) => {
    if (value !== undefined && value !== null && !check(value)) problems.push(what);
  };
  const objectsOf = (value: unknown, field: string, each: (name: string, item: Record<string, any>) => void) => {
    if (value === undefined) return;
    if (!isObject(value)) return problems.push(`${field} is an object of named entries`);
    for (const [name, item] of Object.entries(value)) {
      if (!isObject(item)) problems.push(`${field}.${name} is an object`);
      else each(name, item);
    }
  };
  if (kind === "tiers") {
    optional(document.classifier, isObject, "classifier is an object");
    if (document.tiers !== undefined && !Array.isArray(document.tiers)) problems.push("tiers is a list");
    (Array.isArray(document.tiers) ? document.tiers : []).forEach((tier: unknown, i: number) => {
      if (!isObject(tier)) return problems.push(`tiers[${i}] is an object`);
      if (typeof tier.name !== "string" || !tier.name.trim()) problems.push(`tiers[${i}] has a name`);
      optional(tier.models, isStrings, `tiers[${i}].models is a list of candidates`);
    });
  } else if (kind === "profiles") {
    objectsOf(document.profiles, "profiles", (name, p) => {
      optional(p.tools, isStrings, `profiles.${name}.tools is a list of tool names`);
      optional(p.bash_allow, isStrings, `profiles.${name}.bash_allow is a list of programs`);
      optional(p.resources, isObject, `profiles.${name}.resources is an object`);
    });
  } else if (kind === "repos") {
    objectsOf(document.repos, "repos", (name, r) => optional(r.default_checks, isStrings, `repos.${name}.default_checks is a list of commands`));
  } else if (kind === "task_policy") {
    optional(document.review, isObject, "review is an object");
    if (isObject(document.review)) optional(document.review.reviewers, isStrings, "review.reviewers is a list of candidates");
  }
  return problems;
}
