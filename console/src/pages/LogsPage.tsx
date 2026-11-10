import { useCallback, useEffect, useState } from "react";
import { Badge, Code, Group, Modal, ScrollArea, Select, Switch, Table, Text, TextInput } from "@mantine/core";
import { api, type LogEntry } from "../api";
import { DataTable, Empty, ErrorAlert, PageHeader } from "../components/ui";

const RANGES = [
  { value: "15", label: "Last 15 minutes" },
  { value: "60", label: "Last hour" },
  { value: "360", label: "Last 6 hours" },
  { value: "1440", label: "Last day" },
  { value: "10080", label: "Last week" },
];
const SEVERITIES = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const COLORS: Record<string, string> = { WARNING: "yellow", ERROR: "red", CRITICAL: "red", ALERT: "red", EMERGENCY: "red", INFO: "blue" };
const FOLLOW_MS = 5000;

// The VM's services' logs and the edge's, from Cloud Logging, newest first.
export function LogsPage() {
  const [unit, setUnit] = useState<string | null>(null);
  const [severity, setSeverity] = useState<string | null>(null);
  const [minutes, setMinutes] = useState("60");
  const [q, setQ] = useState("");
  const [follow, setFollow] = useState(false);
  const [units, setUnits] = useState<string[]>([]);
  const [entries, setEntries] = useState<LogEntry[] | null>(null);
  const [open, setOpen] = useState<LogEntry | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const found = await api.logs({ unit: unit ?? undefined, severity: severity ?? undefined, minutes: Number(minutes), q: q.trim() || undefined });
      setEntries(found.entries);
      setUnits(found.units);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [unit, severity, minutes, q]);

  useEffect(() => {
    const wait = setTimeout(() => void load(), 300);
    return () => clearTimeout(wait);
  }, [load]);

  useEffect(() => {
    if (!follow) return;
    const timer = setInterval(() => void load(), FOLLOW_MS);
    return () => clearInterval(timer);
  }, [follow, load]);

  return (
    <>
      <PageHeader title="Logs" description="The VM's services and the edge, from Cloud Logging, newest first. At most 500 entries." />
      <Group mb="md" align="flex-end" wrap="wrap">
        <Select label="Service" placeholder="Every service" clearable value={unit} onChange={setUnit} data={units} w={180} />
        <Select label="At least" placeholder="Any severity" clearable value={severity} onChange={setSeverity} data={SEVERITIES} w={150} />
        <Select label="Time" value={minutes} onChange={(v) => setMinutes(v ?? "60")} data={RANGES} w={170} />
        <TextInput label="Search" placeholder="Text in the entry" value={q} onChange={(e) => setQ(e.currentTarget.value)} style={{ flex: "1 1 200px" }} />
        <Switch label="Follow" checked={follow} onChange={(e) => setFollow(e.currentTarget.checked)} mb={8} />
      </Group>
      <ErrorAlert error={error} />
      {entries && entries.length === 0 && <Empty>No entries match.</Empty>}
      {entries && entries.length > 0 && (
        <DataTable head={["Time", "Service", "Severity", "Message"]}>
          {entries.map((e, i) => (
            <Table.Tr key={`${e.time}-${i}`} onClick={() => setOpen(e)} style={{ cursor: "pointer" }}>
              <Table.Td><Text size="xs" ff="monospace" style={{ whiteSpace: "nowrap" }}>{e.time?.replace("T", " ").slice(0, 19)}</Text></Table.Td>
              <Table.Td><Text size="xs" ff="monospace">{e.service}</Text></Table.Td>
              <Table.Td><Badge size="xs" variant="light" color={COLORS[e.severity] ?? "gray"}>{e.severity}</Badge></Table.Td>
              <Table.Td><Text size="xs" ff="monospace" lineClamp={2}>{e.message}</Text></Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
      )}
      <Modal opened={open !== null} onClose={() => setOpen(null)} title="Log entry" size="xl">
        <ScrollArea.Autosize mah="70vh">
          <Code block>{JSON.stringify(open?.entry, null, 2)}</Code>
        </ScrollArea.Autosize>
      </Modal>
    </>
  );
}
