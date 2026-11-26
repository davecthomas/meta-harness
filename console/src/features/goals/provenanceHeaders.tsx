import type { Goal } from "../../api";
import { InlineField } from "../../components/InlineField";
import { refusal } from "./InlineEdit";

// A goal's provenance setting: whether the files its runs create start with a comment naming the run that made
// them. null leaves it to the deployment's configuration (config/provenance.json), which may differ by repo.
export type Provenance = boolean | null;
export type ProvenanceGoal = Goal & { provenance?: Provenance };

const CHOICES = [
  { value: "configured", label: "Default: the deployment's setting for each repo" },
  { value: "on", label: "On: each new file starts with a comment naming the run that wrote it" },
  { value: "off", label: "Off: new files get no header" },
];

export function provenanceChoice(value: Provenance | undefined): string {
  return value === true ? "on" : value === false ? "off" : "configured";
}

export function provenanceValue(choice: string | null): Provenance {
  return choice === "on" ? true : choice === "off" ? false : null;
}

// The goal's folded settings summary names the setting only when the goal made one.
export function provenanceSummary(value: Provenance | undefined): string[] {
  return value === true ? ["provenance headers on"] : value === false ? ["provenance headers off"] : [];
}

export function ProvenanceEdit(props: { goal: ProvenanceGoal; readOnly: boolean; save: (fields: { provenance: Provenance }) => Promise<unknown> }) {
  return (
    <InlineField
      kind="select"
      label="Mark new files with their run"
      readOnly={props.readOnly}
      value={provenanceChoice(props.goal.provenance)}
      data={CHOICES}
      onCommit={async (choice) => {
        try {
          await props.save({ provenance: provenanceValue(choice) });
          return undefined;
        } catch (err) {
          return refusal(err);
        }
      }}
    />
  );
}
