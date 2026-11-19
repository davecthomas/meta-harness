import { Group, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import { api } from "../../api";
import { InlineField } from "../../components/InlineField";
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

// The engine that routes without a classifier, as the router library spells it (STR_ROUTER_CLASSIFIER_NONE),
// in any capitals: it takes no model.
const NO_CLASSIFIER = "none";

function routesWithoutClassifier(engine: string): boolean {
  return engine.trim().toLowerCase() === NO_CLASSIFIER;
}

// A model field that takes no name: refused is why a typed one is refused, unused why a saved one is marked.
function NoModel(props: {
  model: string;
  empty: string;
  refused: string;
  unused: string;
  changed?: boolean;
  readOnly?: boolean;
  onModel: (model: string) => void;
}) {
  return (
    <div>
      <InlineField
        kind="text"
        label="Classifier model"
        value={props.model}
        empty={props.empty}
        changed={props.changed}
        readOnly={props.readOnly}
        onCommit={(v) => {
          if (v.trim()) return props.refused;
          props.onModel("");
        }}
      />
      {props.model ? (
        <Text size="xs" c="orange" data-testid="classifier-model-unknown">{props.unused}</Text>
      ) : null}
    </div>
  );
}

// The router classifier's engine and model, each picked from the library's list: the models are the chosen
// engine's. An empty engine is the library's default, and an empty model the engine's default. The library's
// default engine is decided by the worker's settings, so its models cannot be listed: while the list is read,
// a model needs an engine picked first, and a saved model with no engine is marked. When the list cannot be
// read, a model is typed. The model of an engine that lists none is typed. A change of engine drops a model the new
// engine does not offer, so the classifier never names a pair that cannot run. Engine none routes without a
// classifier: choosing it drops the model, its model field refuses any name, and a saved one is marked.
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
  const noEngine = !props.engine.trim();
  // An engine the list lacks: no list to pick its model from, so it is typed.
  const models = engines === null ? null : chosen?.models ?? [];
  const pickEngine = (engine: string) => {
    if (routesWithoutClassifier(engine)) return props.onEngine(engine, "");
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
          {routesWithoutClassifier(props.engine) ? (
            <NoModel
              model={props.model}
              empty="none, as no classifier runs"
              refused={`Engine ${props.engine.trim()} routes without a classifier, so it takes no model: leave it empty`}
              unused={`Engine ${props.engine.trim()} takes no model, so ${props.model} is not used`}
              changed={props.modelChanged}
              readOnly={props.readOnly}
              onModel={props.onModel}
            />
          ) : noEngine && engines !== null ? (
            <NoModel
              model={props.model}
              empty="the engine's default"
              refused="Pick a classifier engine first, so the model can be checked against its list, or leave it empty for the engine's default"
              unused={`No classifier engine is picked, so ${props.model} cannot be checked: pick the engine that offers it`}
              changed={props.modelChanged}
              readOnly={props.readOnly}
              onModel={props.onModel}
            />
          ) : (
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
          )}
        </Group>
      </Group>
      <Unreadable what="classifier engines" error={error} />
    </Stack>
  );
}
