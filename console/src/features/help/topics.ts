// What Help says, section by section, in reading order on its one page: why the meta-harness exists, the rules
// it follows and what a goal is come first. The rules are the design's (docs/technical-design.md, Goal seeking); a topic says only
// what the meta-harness does today, and a rule not fully built is named as such.

export type Block =
  | { kind: "text"; text: string }
  | { kind: "steps"; items: string[] }
  | { kind: "terms"; items: [string, string][] }
  | { kind: "note"; text: string };

export interface Topic {
  slug: string;
  title: string;
  blocks: Block[];
}

export const TOPICS: Topic[] = [
  {
    slug: "why",
    title: "Why",
    blocks: [
      { kind: "text", text: "Most code changes spend much of their time waiting. A pull request conflicts with main and sits. A check fails for a reason unrelated to the change. A review asks for one more fix, then another. Each stop waits until a person notices it." },
      { kind: "text", text: "The meta-harness clears most of those stops itself. You say what you want done, and it writes the code, reviews it, fixes what the review finds and merges it. It comes to you when a decision is yours to make." },
    ],
  },
  {
    slug: "first-principles",
    title: "First principles",
    blocks: [
      {
        kind: "steps",
        items: [
          "The goal is merged work that makes main better. A task that has stopped is a fault in the system, and each stop has an owner and a next step.",
          "A pull request merges when main is better with it than without it. Quality comes from many pull requests built on each other.",
          "Progress stays done. A review that passed a commit stands for that commit, and a retry starts where the last try stopped.",
          "It takes the clear next step without asking. That includes resolving a conflict and rerunning a flaky check.",
          "It does not repeat a failing step without limit. It sends a task back at most three times, and changes the step where it can.",
          "It asks you only for a decision or a permission that is yours to give.",
          "It shows every decision, with what it saw and the reason for it.",
          "You can always decide differently. The recommended action is one of several you can take.",
          "A spend limit you set is never passed. Reaching one stops the work and asks you.",
        ],
      },
    ],
  },
  {
    slug: "the-goal",
    title: "The goal",
    blocks: [
      { kind: "text", text: "A goal is what you want done, in your words, with the repos it touches and, if you want one, a budget. \"Add CSV export to the reports page\" is a goal." },
      { kind: "text", text: "The Goal Planner turns a goal into tasks. Each task is one change, small enough to review, and each ends as a merged pull request. The goal is met when its tasks have merged." },
      { kind: "text", text: "When a task stops, the meta-harness asks what would get the task merged." },
    ],
  },
  {
    slug: "who-is-talking",
    title: "Who is talking",
    blocks: [
      { kind: "text", text: "The Goal Planner and the Task Shepherd talk to you. Each has its own mark and color, shown beside what it says." },
      {
        kind: "terms",
        items: [
          ["The Goal Planner", "decides what tasks a goal needs and the order they run in. It speaks on a goal's page."],
          ["The Task Shepherd", "gets each task merged. It acts when a task stops, and asks you when it must. It speaks on a task's page."],
        ],
      },
    ],
  },
  {
    slug: "stuck-tasks",
    title: "How a stuck task gets moving",
    blocks: [
      { kind: "text", text: "For a task that has stopped, the Task Shepherd takes the first action in this list that applies:" },
      {
        kind: "steps",
        items: [
          "Merge it, when a review and its checks have passed.",
          "Clear what blocks the merge, such as a conflict with main.",
          "Fix what the review found.",
          "Try again with a stronger model, once.",
          "Ask you.",
        ],
      },
      { kind: "text", text: "It tries a task at most three times by itself. After that the task is yours." },
      { kind: "text", text: "Its panel at the top of the task's page then says what happened and what it tried. Under that are the actions that remain, as buttons. The first button is the recommended one." },
      { kind: "text", text: "Each button does the whole job. Resolve the conflict and merge starts a fix run for the conflict, then merges once a review is clean." },
      { kind: "text", text: "The order depends on how the pull request stands now, such as whether it conflicts with main." },
    ],
  },
  {
    slug: "spend-limits",
    title: "Spend limits",
    blocks: [
      { kind: "text", text: "You can set a spend limit on a task and a budget on a goal. Both are optional. The meta-harness never goes past one you set." },
      { kind: "text", text: "A task that reaches its limit stops and asks you. You can raise the limit from its page and the task continues." },
    ],
  },
];

