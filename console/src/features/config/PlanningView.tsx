import { Badge, Code, Group, Stack, Text } from "@mantine/core";
import { InlineField } from "../../components/InlineField";
import { Facts } from "../../components/ui";
import { SimilarGoalsSettings } from "./SimilarGoalsSettings";
import { changed, type Staging } from "./ConfigViews";
import { joinCandidate, splitCandidate } from "./staging";

// With the value itself first when the list does not name it, so a select can show what the document holds.
function withValue(options: string[], value: string): string[] {
  return !value || options.includes(value) ? options : [value, ...options];
}

const TONES = [
  { value: "casual", label: "Casual and friendly" },
  { value: "balanced", label: "Plain and professional" },
  { value: "formal", label: "Formal" },
];
const DETAILS = [
  { value: "low", label: "Low detail" },
  { value: "medium", label: "Medium detail" },
  { value: "high", label: "High detail" },
];

const GOALS = [
  { value: "all", label: "Every goal" },
  { value: "from_description", label: "Only goals written as a description" },
];

// The planning configuration: who the planner is (the harness profile and model a planning task runs on), what
// one planning task may spend, which goals are planned before their tasks run, where a plan's new tasks go
// unless the operator says otherwise, and how much of a goal's issues the planner is shown.
// profiles: the harness profiles a planning task can run on (those with the create_issue tool, which a
// planning run records its plan with). models: every model the configuration prices.
export function PlanningView({ staging: s, profiles, models }: { staging: Staging; profiles: string[]; models: string[] }) {
  const doc = s.document;
  const { profile, model } = splitCandidate(String(doc.candidate ?? ""));
  const everyGoal = (doc.goals ?? "from_description") === "all";
  const tracks = doc.track_new_tasks !== false;
  const updatesOn = doc.updates?.enabled !== false;
  return (
    <Facts
      items={[
        [
          "The planner",
          <Stack gap={4}>
            <Group gap="xs">
              <InlineField
                kind="select"
                label="Planner's harness profile"
                value={profile || null}
                data={withValue(profiles, profile)}
                display={profile ? <Badge variant="dot" tt="none">{profile}</Badge> : undefined}
                empty="pick a profile"
                changed={changed(s, ["candidate"])}
                readOnly={s.readOnly}
                onCommit={(v) => s.stage(["candidate"], joinCandidate(v ?? "", model))}
              />
              <InlineField
                kind="select"
                searchable
                label="Planner's model"
                value={model || null}
                data={withValue(models, model)}
                display={model ? <Code>{model}</Code> : undefined}
                empty="pick a model"
                changed={changed(s, ["candidate"])}
                reasons={s.reasons("candidate")}
                readOnly={s.readOnly}
                onCommit={(v) => s.stage(["candidate"], joinCandidate(profile, v ?? ""))}
              />
            </Group>
            <Text size="sm" c="dimmed">
              Its profile needs the create_issue tool.
            </Text>
          </Stack>,
        ],
        [
          "Which goals are planned",
          <Stack gap={4}>
            <InlineField
              kind="select"
              label="Which goals are planned"
              value={doc.goals ?? "from_description"}
              data={GOALS}
              changed={changed(s, ["goals"])}
              reasons={s.reasons("goals")}
              readOnly={s.readOnly}
              onCommit={(v) => v && s.stage(["goals"], v)}
            />
            <Text size="sm" c="dimmed">
              {everyGoal
                ? "Nothing starts until you approve the goal's plan."
                : "A goal made from existing issues runs them without a plan."}
            </Text>
          </Stack>,
        ],
        [
          "A plan's new tasks",
          <Stack gap={4}>
            <InlineField
              kind="switch"
              label="A plan's new tasks go to the issue tracker"
              nameByLabel
              value={tracks}
              switchLabel={tracks ? "Added to the goal's issue tracker" : "Kept in Muxxy alone"}
              changed={changed(s, ["track_new_tasks"])}
              reasons={s.reasons("track_new_tasks")}
              readOnly={s.readOnly}
              onCommit={(on) => s.stage(["track_new_tasks"], on)}
            />
          </Stack>,
        ],
        [
          "How the planner talks",
          <Stack gap={4}>
            <Group gap="lg">
              <InlineField
                kind="select"
                label="Planner's tone of voice"
                value={doc.voice?.tone ?? "casual"}
                data={TONES}
                changed={changed(s, ["voice", "tone"])}
                readOnly={s.readOnly}
                onCommit={(v) => v && s.stage(["voice", "tone"], v)}
              />
              <InlineField
                kind="select"
                label="Technical detail the planner shares"
                value={doc.voice?.detail ?? "low"}
                data={DETAILS}
                changed={changed(s, ["voice", "detail"])}
                reasons={s.reasons("voice")}
                readOnly={s.readOnly}
                onCommit={(v) => v && s.stage(["voice", "detail"], v)}
              />
            </Group>
            <Text size="sm" c="dimmed">
              Applies to the planner's updates and plan summaries. Task descriptions for agents stay exact.
            </Text>
          </Stack>,
        ],
        [
          "The planner's updates",
          <Stack gap={4}>
            <InlineField
              kind="switch"
              label="The planner writes updates on each goal's page"
              nameByLabel
              value={updatesOn}
              switchLabel={updatesOn ? "Written as each goal moves" : "Off"}
              changed={changed(s, ["updates", "enabled"])}
              reasons={s.reasons("updates")}
              readOnly={s.readOnly}
              onCommit={(on) => s.stage(["updates", "enabled"], on)}
            />
            <Group gap="lg">
              <InlineField
                kind="select"
                searchable
                label="Model that writes the updates"
                value={doc.updates?.model || null}
                data={withValue(models, String(doc.updates?.model ?? ""))}
                display={doc.updates?.model ? <Code>{doc.updates.model}</Code> : undefined}
                empty="goal drafting's model"
                changed={changed(s, ["updates", "model"])}
                readOnly={s.readOnly}
                onCommit={(v) => s.stage(["updates", "model"], v ?? undefined)}
              />
              <InlineField
                kind="number"
                label="Most updates a goal may have"
                value={doc.updates?.max_per_goal ?? 60}
                min={1}
                decimalScale={0}
                suffix=" a goal at most"
                changed={changed(s, ["updates", "max_per_goal"])}
                readOnly={s.readOnly}
                onCommit={(v) => s.stage(["updates", "max_per_goal"], v ?? undefined)}
              />
            </Group>
          </Stack>,
        ],
        [
          "Suggested fixes",
          <Stack gap={4}>
            <InlineField
              kind="switch"
              label="The planner drafts fixes to its plans"
              nameByLabel
              value={doc.suggest_fixes !== false}
              switchLabel={doc.suggest_fixes !== false ? "On" : "Off"}
              changed={changed(s, ["suggest_fixes"])}
              reasons={s.reasons("suggest_fixes")}
              readOnly={s.readOnly}
              onCommit={(on) => s.stage(["suggest_fixes"], on)}
            />
            <Text size="sm" c="dimmed">Each fix is one more planning run.</Text>
          </Stack>,
        ],
        [
          "Spend limit",
          <Stack gap={4}>
            <InlineField
              kind="number"
              label="A planning task's spend limit"
              value={doc.max_usd === undefined || doc.max_usd === null ? null : Number(doc.max_usd)}
              min={0.01}
              decimalScale={2}
              prefix="$"
              changed={changed(s, ["max_usd"])}
              reasons={s.reasons("max_usd")}
              readOnly={s.readOnly}
              onCommit={(v) => s.stage(["max_usd"], v === null ? undefined : Number(v).toFixed(2))}
            />
            <Text size="sm" c="dimmed">For each planning run.</Text>
          </Stack>,
        ],
        ["Similar goals", <SimilarGoalsSettings staging={s} />],
        [
          "Spend limit without asking",
          <Stack gap={4}>
            <InlineField
              kind="number"
              label="What planning may spend on a goal without asking"
              value={doc.max_unasked_usd === undefined || doc.max_unasked_usd === null ? 12 : Number(doc.max_unasked_usd)}
              min={0.01}
              decimalScale={2}
              prefix="$"
              changed={changed(s, ["max_unasked_usd"])}
              reasons={s.reasons("max_unasked_usd")}
              readOnly={s.readOnly}
              onCommit={(v) => s.stage(["max_unasked_usd"], v === null ? undefined : Number(v).toFixed(2))}
            />
            <Text size="sm" c="dimmed">Covers continuing a stopped plan, resizing tasks and fixing gaps. Resets each time you ask for a planning run.</Text>
          </Stack>,
        ],
        [
          "What the planner is shown",
          <Stack gap={4}>
            <Group gap="lg">
              <InlineField
                kind="number"
                label="Issues shown to the planner"
                value={doc.max_issues ?? 60}
                min={1}
                decimalScale={0}
                suffix=" issues"
                changed={changed(s, ["max_issues"])}
                reasons={s.reasons("max_issues")}
                readOnly={s.readOnly}
                onCommit={(v) => s.stage(["max_issues"], v ?? undefined)}
              />
              <InlineField
                kind="number"
                label="Characters of each issue shown to the planner"
                value={doc.issue_chars ?? 4000}
                min={1}
                decimalScale={0}
                suffix=" characters of each"
                changed={changed(s, ["issue_chars"])}
                reasons={s.reasons("issue_chars")}
                readOnly={s.readOnly}
                onCommit={(v) => s.stage(["issue_chars"], v ?? undefined)}
              />
            </Group>
          </Stack>,
        ],
      ]}
    />
  );
}
