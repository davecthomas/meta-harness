import { Badge, Text, Tooltip } from "@mantine/core";

// Plain words for the values a task's Reviews and Attempts tabs show, in place of the service's own names:
// a review's verdict, how an attempt ended, and the harness profile and model that did the work.

interface Words {
  label: string;
  meaning: string;
  color: string;
}

const VERDICTS: Record<string, Words> = {
  clean: { label: "Clean", color: "teal", meaning: "The reviewer found nothing to change at this commit." },
  changes: { label: "Changes asked for", color: "orange", meaning: "The reviewer left findings, listed below, for a fix run or a person to settle." },
};

// What became of a review finding: the next round's reviewer's judgment, or an operator's dismissal.
const OUTCOMES: Record<string, Words> = {
  fixed: { label: "Fixed", color: "teal", meaning: "The next round's reviewer judged that the fix run fixed it." },
  not_fixed: { label: "Not fixed", color: "orange", meaning: "The next round's reviewer judged that it was still there after the fix run." },
};

// How one attempt of a task ended (the attempt_finished event's decision).
const DECISIONS: Record<string, Words> = {
  finished: { label: "Finished", color: "teal", meaning: "Its run finished the work." },
  next_candidate: { label: "Did not finish", color: "orange", meaning: "Its run did not finish the work, so the tier's next candidate was tried." },
  final: { label: "Did not finish, the last try", color: "red", meaning: "Its run did not finish the work, and no candidate was left to try." },
};

// A value the table does not hold reads as its own name, spaced, so a new one is never hidden.
function wordsFor(table: Record<string, Words>, value: string): Words {
  const spaced = value.replace(/_/g, " ");
  return table[value] ?? { label: spaced.charAt(0).toUpperCase() + spaced.slice(1), meaning: "", color: "gray" };
}

function WordBadge({ words }: { words: Words }) {
  const badge = <Badge color={words.color} variant="light" tt="none">{words.label}</Badge>;
  return words.meaning ? <Tooltip label={words.meaning} multiline maw={280}>{badge}</Tooltip> : badge;
}

export function VerdictBadge({ verdict }: { verdict: string }) {
  return <WordBadge words={wordsFor(VERDICTS, verdict)} />;
}

// Nothing for a finding no reviewer has judged yet, or one an operator dismissed (its own badge says so).
export function OutcomeBadge({ outcome }: { outcome?: string | null }) {
  if (!outcome || outcome === "dismissed") return null;
  return <WordBadge words={wordsFor(OUTCOMES, outcome)} />;
}

export function DecisionBadge({ decision }: { decision: string }) {
  return <WordBadge words={wordsFor(DECISIONS, decision)} />;
}

// The parts of harness:<profile>/<engine>:<model>, the pair that names who ran a review or an attempt.
export function candidateParts(pair: string): { profile: string; engine: string; model: string } {
  const [profile, ...rest] = pair.replace(/^harness:/, "").split("/");
  const inner = rest.join("/");
  const colon = inner.indexOf(":");
  return { profile: profile ?? "", engine: colon < 0 ? "" : inner.slice(0, colon), model: colon < 0 ? inner : inner.slice(colon + 1) };
}

// Who did the work, as its model and its harness profile; the pair as the configuration writes it is the
// hover text, for finding it under Configuration.
export function CandidateName({ pair, size = "xs" }: { pair: string | null | undefined; size?: "xs" | "sm" }) {
  if (!pair) return null;
  const { profile, model } = candidateParts(pair);
  if (!profile || !model) return <Text size={size} c="dimmed" ff="monospace">{pair}</Text>;
  return (
    <Text size={size} c="dimmed" title={pair} data-testid="candidate-name">
      <Text span size={size} ff="monospace">{model}</Text>, profile <Text span size={size} ff="monospace">{profile}</Text>
    </Text>
  );
}
