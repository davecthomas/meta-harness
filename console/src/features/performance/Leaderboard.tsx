import { useState } from "react";
import { Anchor, Card, Group, Modal, Stack, Table, Text } from "@mantine/core";
import type { Board, BoardRow } from "./api";

// Where Muxxy's dollar-hours per successful task stands among measured harness and model pairs: its rank as a
// big number, the whole board in a dialog with Muxxy in its place, and every figure on one line, on a
// logarithmic scale, since they span from under 1 to several hundred. Lower is better.

const figure = (value: number) => (value >= 100 ? value.toFixed(0) : value >= 10 ? value.toFixed(1) : value.toFixed(2));
const nameOf = (row: BoardRow) => (row.muxxy ? "Muxxy" : `${row.harness} · ${row.model}`);

// The same caveat wherever the board is shown: the two sets of figures are not over the same tasks.
function Candid({ board }: { board: Board }) {
  return (
    <Text size="xs" c="dimmed" data-testid="board-candid">
      The other figures were measured on one fixed set of tasks. Muxxy's is over its own {board.tasks} task{board.tasks === 1 ? "" : "s"} in
      this time, which are different tasks, so this is a place among them and not a like-for-like result.
    </Text>
  );
}

// The decades the scale's ticks sit on, from the one at or under the smallest figure to the one at or over the largest.
export function decades(values: number[]): number[] {
  const low = Math.floor(Math.log10(Math.min(...values)));
  const high = Math.ceil(Math.log10(Math.max(...values)));
  return Array.from({ length: Math.max(1, high - low) + 1 }, (_, i) => 10 ** (low + i));
}

// How far along the scale a figure sits, from 0 to 1: equal steps are equal multiples.
export function along(value: number, ticks: number[]): number {
  const low = Math.log10(ticks[0]);
  const high = Math.log10(ticks[ticks.length - 1]);
  return Math.min(1, Math.max(0, (Math.log10(value) - low) / (high - low)));
}

export function BoardChart({ board }: { board: Board }) {
  if (board.rows.length === 0) return null;
  const ticks = decades(board.rows.map((r) => r.value));
  return (
    <Card withBorder padding="lg" mb="lg" data-testid="board-chart">
      <Group justify="space-between" align="baseline" wrap="wrap" gap="xs" mb="sm">
        <Text size="xs" c="dimmed" tt="uppercase" fw={600}>Dollar-hours per successful task, against measured harness and model pairs</Text>
        <Text size="xs" c="dimmed">Lower is better. Logarithmic: each mark is ten times the one before.</Text>
      </Group>
      <Stack gap={4} role="list" aria-label="Dollar-hours per successful task, cheapest first">
        {board.rows.map((row) => (
          <Group key={`${row.rank}`} gap="sm" wrap="nowrap" role="listitem" data-testid={row.muxxy ? "board-bar-muxxy" : "board-bar"}>
            <Text size="xs" w={210} ta="right" fw={row.muxxy ? 700 : 400} c={row.muxxy ? undefined : "dimmed"} lineClamp={1} style={{ flex: "none" }}>
              {nameOf(row)}
            </Text>
            <div style={{ position: "relative", flex: 1, height: 16, borderLeft: "1px solid var(--mantine-color-default-border)" }}>
              {ticks.slice(1).map((tick) => (
                <div key={tick} aria-hidden style={{ position: "absolute", left: `${along(tick, ticks) * 100}%`, top: 0, bottom: 0, borderLeft: "1px dashed var(--mantine-color-default-border)" }} />
              ))}
              <div
                style={{
                  position: "absolute", left: 0, top: 3, height: 10, borderRadius: 2,
                  width: `max(3px, ${along(row.value, ticks) * 100}%)`,
                  background: row.muxxy ? "var(--mantine-color-violet-5)" : "var(--mantine-color-gray-6)",
                }}
              />
            </div>
            <Text size="xs" w={52} fw={row.muxxy ? 700 : 400} style={{ flex: "none", fontVariantNumeric: "tabular-nums" }}>{figure(row.value)}</Text>
          </Group>
        ))}
        <Group gap="sm" wrap="nowrap" aria-hidden>
          <div style={{ width: 210, flex: "none" }} />
          <div style={{ position: "relative", flex: 1, height: 16 }}>
            {ticks.map((tick) => (
              <Text key={tick} size="xs" c="dimmed" style={{ position: "absolute", left: `${along(tick, ticks) * 100}%`, transform: "translateX(-50%)" }}>
                {tick >= 1 ? tick : tick.toString()}
              </Text>
            ))}
          </div>
          <div style={{ width: 52, flex: "none" }} />
        </Group>
      </Stack>
      <Candid board={board} />
    </Card>
  );
}

// The rank, large, with the board one press away.
export function RankCard({ board }: { board: Board }) {
  const [open, setOpen] = useState(false);
  const mine = board.rows.find((r) => r.muxxy);
  if (board.rows.length === 0) return null;
  return (
    <Card withBorder padding="lg" data-testid="board-rank">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>Rank on dollar-hours</Text>
      <Text fz={32} fw={700} lh={1.1} mt={6}>{board.rank === null ? "none" : `${board.rank} of ${board.of}`}</Text>
      <Text size="xs" c="dimmed" mt={6}>
        {mine && board.multiple_of_best !== null
          ? board.rank === 1
            ? `The cheapest, at ${figure(mine.value)}`
            : `${figure(mine.value)}, ${board.multiple_of_best >= 10 ? Math.round(board.multiple_of_best) : board.multiple_of_best.toFixed(1)} times the cheapest`
          : "No finished task in this time to rank"}
        {" · "}
        <Anchor component="button" type="button" size="xs" onClick={() => setOpen(true)}>Show the leaderboard</Anchor>
      </Text>
      <Modal opened={open} onClose={() => setOpen(false)} title="Dollar-hours per successful task" size="lg">
        <Stack gap="sm">
          <Table striped withTableBorder data-testid="board-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={60}>Rank</Table.Th>
                <Table.Th>Harness and model</Table.Th>
                <Table.Th ta="right">Dollar-hours</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {board.rows.map((row) => (
                <Table.Tr key={row.rank} data-testid={row.muxxy ? "board-row-muxxy" : "board-row"} style={row.muxxy ? { background: "var(--mantine-color-violet-light)" } : undefined}>
                  <Table.Td fw={row.muxxy ? 700 : 400}>{row.rank}</Table.Td>
                  <Table.Td fw={row.muxxy ? 700 : 400}>{nameOf(row)}</Table.Td>
                  <Table.Td ta="right" fw={row.muxxy ? 700 : 400} style={{ fontVariantNumeric: "tabular-nums" }}>{figure(row.value)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text size="xs" c="dimmed">Lower is better.</Text>
          <Candid board={board} />
        </Stack>
      </Modal>
    </Card>
  );
}
