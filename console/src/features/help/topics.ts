// What Help says, topic by topic, in reading order: why the meta-harness exists, the rules it follows and what
// a goal is come first. The rules are the design's (docs/technical-design.md, Goal seeking); a topic says only
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
      { kind: "text", text: "Most of the time a code change takes is waiting. A pull request conflicts with main and sits. A check fails for a reason unrelated to the change. A review asks for one more fix, then another. Each stop needs a person to see it, decide and act." },
      { kind: "text", text: "The meta-harness removes those stops. You say what you want done, and it writes the code, reviews it, fixes what the review finds and merges it. It comes to you when a decision is yours to make." },
    ],
  },
  {
    slug: "first-principles",
    title: "First principles",
    blocks: [
      { kind: "text", text: "The meta-harness follows nine rules. Every part of it has to follow them." },
      {
        kind: "steps",
        items: [
          "The goal is merged work that makes main better. A task that has stopped is a fault in the system, and each stop has an owner and a next step.",
          "Better beats perfect. A pull request merges when main is better with it than without it. Quality comes from many pull requests built on each other.",
          "Progress is kept. A review that passed a commit stands for that commit, and a retry starts where the last try stopped.",
          "It takes the clear next step without asking. That covers resolving a conflict, running a flaky check again and retrying after an outage.",
          "It does not try a step a third time after the step failed the same way twice. It takes a different step.",
          "It asks you only for what a person alone can give: a call on value or risk, more money, an approval or a credential.",
          "It shows every decision: what it saw, what it chose and why.",
          "You can always decide differently. Every action stays open to you.",
          "Spend limits are hard. Reaching one stops the work and asks you.",
        ],
      },
      { kind: "note", text: "Rule 5 is not fully built yet: a failing step can repeat up to three times." },
    ],
  },
  {
    slug: "the-goal",
    title: "The goal",
    blocks: [
      { kind: "text", text: "A goal is what you want done, in your words, with the repos it touches and a budget. \"Add CSV export to the reports page\" is a goal." },
      { kind: "text", text: "The Goal Planner turns a goal into tasks. Each task is one change, small enough to review, and each ends as a merged pull request. The goal is met when its tasks have merged." },
      { kind: "text", text: "Everything the meta-harness does serves that end. When a task stops, it asks what would get the task merged." },
    ],
  },
  {
    slug: "who-is-talking",
    title: "Who is talking",
    blocks: [
      { kind: "text", text: "Two parts of the meta-harness talk to you." },
      {
        kind: "terms",
        items: [
          ["The Goal Planner", "decides what work a goal needs: the tasks, their size and their order. It speaks on a goal's page."],
          ["The Task Shepherd", "gets each task to merged. It watches every open task, acts when a task stops, and asks you when it must. It speaks on a task's page."],
        ],
      },
    ],
  },
  {
    slug: "stuck-tasks",
    title: "How a stuck task gets moving",
    blocks: [
      { kind: "text", text: "For a task that has stopped, the Task Shepherd takes the first move in this list that applies:" },
      {
        kind: "steps",
        items: [
          "Merge it, when a review and its checks have passed.",
          "Clear what blocks the merge, such as a conflict with main.",
          "Fix what the review found.",
          "Try again with a stronger model, once.",
          "Ask you, with the moves that remain as buttons.",
        ],
      },
      { kind: "text", text: "It tries a task at most three times by itself. After that the task is yours, and its page says what the Task Shepherd tried." },
    ],
  },
  {
    slug: "when-it-asks-you",
    title: "When it asks you",
    blocks: [
      { kind: "text", text: "An ask says what happened in a line or two. Under it are the actions you can take. The first button is the recommended one." },
      { kind: "text", text: "Each button does the whole job. Retry runs the task again. Merge resolves a conflict first when there is one, then merges once a review is clean." },
    ],
  },
  {
    slug: "spend-limits",
    title: "Spend limits",
    blocks: [
      { kind: "text", text: "Every task and every goal has a spend limit in dollars. The meta-harness never goes past one by itself." },
      { kind: "text", text: "A task that reaches its limit stops and asks you. You can raise the limit from its page and the task continues." },
    ],
  },
];

export function topic(slug: string | undefined): Topic | undefined {
  return TOPICS.find((t) => t.slug === slug);
}
