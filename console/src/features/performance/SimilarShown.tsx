import { useEffect, useState } from "react";
import { Anchor, Badge, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { call } from "../../client";
import { SectionTitle, when } from "../../components/ui";

// Each goal New goal showed as similar to one being written, with its score and what the operator said of it
// (docs/specs/similar-goals.md). It is what the cut-off is set from: where "same work" and "different" part is
// the number to use.

export interface ShownRow {
  goal_id: string;
  title: string;
  score: number | null;
  answer: string;
  new_goal_id: string;
  by: string;
  at: string;
}

export const similarShownApi = {
  similarShown: () => call<{ shown: ShownRow[]; cutoff: number; model: string }>("/v1/goals/similar/shown"),
};

// What an operator said, in the page's words, and how it counts toward the cut-off.
const ANSWERS: Record<string, { label: string; color: string }> = {
  same_work: { label: "Same work", color: "teal" },
  opened: { label: "Opened it", color: "blue" },
  different: { label: "Different", color: "orange" },
  not_duplicate: { label: "Not a duplicate", color: "orange" },
  shown: { label: "Shown, no answer", color: "gray" },
};

// The rows shown before the table says how many more there are.
export const ROWS_SHOWN = 25;

export function SimilarShown() {
  const [found, setFound] = useState<{ shown: ShownRow[]; cutoff: number; model: string } | null>(null);
  useEffect(() => {
    // An answer that comes after the page has gone is dropped.
    let open = true;
    similarShownApi.similarShown().then((found) => open && setFound(found), () => open && setFound(null));
    return () => {
      open = false;
    };
  }, []);
  // A page with nothing recorded yet says nothing: the table is for tuning, once there is something to tune by.
  if (!found || !Array.isArray(found.shown) || found.shown.length === 0) return null;
  const rows = found.shown.slice(0, ROWS_SHOWN);
  return (
    <div data-testid="similar-shown">
      <SectionTitle>Similar goals shown</SectionTitle>
      <Text size="sm" c="dimmed" mb="sm">
        Goals at {Math.round(found.cutoff * 100)}% similar or more show on New goal. Set the number in{" "}
        <Anchor component={Link} to="/config?kind=planning" size="sm">Configuration, Planning</Anchor>, where "Same work" and "Different" part.
      </Text>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Goal shown</Table.Th>
            <Table.Th>Similar</Table.Th>
            <Table.Th>What was said</Table.Th>
            <Table.Th>When</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row, i) => {
            const said = ANSWERS[row.answer] ?? { label: row.answer, color: "gray" };
            return (
              <Table.Tr key={`${row.goal_id}-${row.at}-${i}`}>
                <Table.Td><Anchor component={Link} to={`/goals/${row.goal_id}`} size="sm">{row.title || row.goal_id}</Anchor></Table.Td>
                <Table.Td>{row.score === null ? "" : `${Math.round(row.score * 100)}%`}</Table.Td>
                <Table.Td><Badge variant="light" color={said.color} tt="none" radius="sm">{said.label}</Badge></Table.Td>
                <Table.Td><Text size="sm" c="dimmed">{row.at ? when(row.at) : ""}</Text></Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      {found.shown.length > rows.length && <Text size="sm" c="dimmed" mt="xs">And {found.shown.length - rows.length} earlier.</Text>}
    </div>
  );
}
