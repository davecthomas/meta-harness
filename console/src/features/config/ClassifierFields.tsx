import { Group, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import { api } from "../../api";
import { PickedName, Unreadable } from "../../components/PickedName";
import type { ClassifierEngine } from "./api";

// The classifier engines the router library offers, each with its models (GET /v1/config/classifiers): null
// until they arrive, and error when they cannot be read. Read only when asked for, by the tiers view.
export interface ClassifierList {
  engines: ClassifierEngine[] | null;
  error: string | null;
}

export function useClassifierEngines(wanted: boolean): ClassifierList {
  const [engines, setEngines] = useState<ClassifierEngine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!wanted) return;
    let stale = false;
    setEngines(null);
    setError(null);
    api.configClassifiers().then(
      (found) => !stale && setEngines(found),
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [wanted]);
  return { engines, error };
}

// The router classifier's engine and model, each picked from the library's list: the models are the chosen
// engine's. An empty engine is the library's default, and an empty model the engine's default; so is a model
// with no engine, or of an engine that lists none, which is typed. A change of engine drops a model the new
// engine does not offer, so the classifier never names a pair that cannot run.
export function ClassifierFields(props: {
  engine: string;
  model: string;
  list: ClassifierList;
  engineChanged?: boolean;
  modelChanged?: boolean;
  readOnly?: boolean;
  onEngine: (engine: string, model: string) => void;
  onModel: (model: string) => void;
}) {
  const { engines, error } = props.list;
  const chosen = engines?.find((e) => e.engine.toLowerCase() === props.engine.trim().toLowerCase());
  // No engine, or one the list lacks: no list to pick its model from, so it is typed.
  const models = engines === null ? null : chosen?.models ?? [];
  const pickEngine = (engine: string) => {
    const offered = engines?.find((e) => e.engine === engine)?.models ?? [];
    const keep = !props.model || !offered.length || offered.some((m) => m.toLowerCase() === props.model.toLowerCase());
    props.onEngine(engine, keep ? props.model : "");
  };
  return (
    <Stack gap={4}>
      <Group gap="lg" wrap="wrap" align="flex-start">
        <Group gap={6} wrap="nowrap" align="flex-start">
          <Text size="xs" c="dimmed" mt={4}>Engine</Text>
          <PickedName
            label="Classifier engine"
            what="classifier engine"
            where="The router library"
            value={props.engine}
            empty="the library's default"
            known={engines?.map((e) => e.engine) ?? null}
            unreadable={error}
            testId="classifier-engine-unknown"
            changed={props.engineChanged}
            readOnly={props.readOnly}
            onCommit={pickEngine}
          />
        </Group>
        <Group gap={6} wrap="nowrap" align="flex-start">
          <Text size="xs" c="dimmed" mt={4}>Model</Text>
          <PickedName
            label="Classifier model"
            what="model"
            where={`Engine ${chosen?.engine ?? props.engine}`}
            value={props.model}
            empty="the engine's default"
            known={models}
            unreadable={error}
            testId="classifier-model-unknown"
            changed={props.modelChanged}
            readOnly={props.readOnly}
            onCommit={props.onModel}
          />
        </Group>
      </Group>
      <Unreadable what="classifier engines" error={error} />
    </Stack>
  );
}
