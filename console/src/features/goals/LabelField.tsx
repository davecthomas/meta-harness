import { useEffect, useMemo, useState } from "react";
import { Autocomplete, Group, Stack, Text } from "@mantine/core";
import { api, type LabelCount } from "../../api";

// A label for the goal, suggested from the labels on the open issues of the picked repos (every
// registered repo when none is picked). Each suggestion shows its open-issue count, and is marked when
// open goals already hold all its issues. A label with no open issue can still be typed, and the field
// says so, so a typo shows. If the labels can't be read, the field is plain text and says why.
export function LabelField({ value, onChange, repos, required = true }: { value: string; onChange: (label: string) => void; repos: string[]; required?: boolean }) {
  const [labels, setLabels] = useState<LabelCount[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.labels().then(
      (r) => setLabels(r.labels),
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);

  const options = useMemo(() => suggestions(labels ?? [], repos), [labels, repos]);
  const byName = useMemo(() => new Map(options.map((o) => [o.label, o])), [options]);

  const typed = value.trim();
  let hint: string | null = null;
  if (error) hint = `Labels could not be loaded, so type it exactly: ${error}`;
  else if (labels && typed && !byName.has(typed)) hint = "No open issue has this label yet.";

  return (
    <Stack gap={4}>
      <Autocomplete
        label="Label"
        description="An open issue with this label, in any of the goal's repos, becomes one of its tasks."
        required={required}
        placeholder="meta-harness:bootstrap"
        value={value}
        onChange={onChange}
        data={options.map((o) => o.label)}
        maxDropdownHeight={280}
        renderOption={({ option }) => {
          const row = byName.get(option.value);
          if (!row) return option.value;
          return (
            <Group justify="space-between" wrap="nowrap" gap="md" w="100%">
              <Text size="sm" ff="monospace">{row.label}</Text>
              <Text size="xs" c={row.allHeld ? "orange" : "dimmed"} ta="right">
                {row.count} open{row.allHeld ? ` · all held by goal ${row.heldBy.join(", ")}` : ""} · {row.repos.join(", ")}
              </Text>
            </Group>
          );
        }}
      />
      {/* Advice, not a validation error: the label is still allowed. */}
      {hint && <Text size="xs" c={error ? "red" : "orange"}>{hint}</Text>}
    </Stack>
  );
}

export interface LabelSuggestion {
  label: string;
  count: number;
  repos: string[];
  allHeld: boolean;
  heldBy: string[];
}

// The labels in the picked repos (all of them when none is picked), counted in those repos, most first.
export function suggestions(labels: LabelCount[], picked: string[]): LabelSuggestion[] {
  const rows: LabelSuggestion[] = [];
  for (const row of labels) {
    const repos = Object.keys(row.repos).filter((r) => !picked.length || picked.includes(r)).sort();
    if (!repos.length) continue;
    const count = repos.reduce((n, r) => n + row.repos[r], 0);
    // Held counts span every repo: a label whose issues are all held is all held in the picked repos too.
    const allHeld = row.held > 0 && row.held >= row.total;
    rows.push({ label: row.label, count, repos, allHeld, heldBy: row.held_by });
  }
  return rows.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}
