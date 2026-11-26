import { useEffect, useState } from "react";
import { Group, Stack, Text } from "@mantine/core";
import { call } from "../../client";
import { InlineField } from "../../components/InlineField";
import { changed, type Staging } from "./ConfigViews";

// The planning configuration's `similar_goals`: whether New goal looks for open goals like the one being
// written, which embedding model compares them, how alike a goal must be to show, and how many show. The model
// is picked from the embedding models the configuration prices.

export const similarSettingsApi = {
  embeddingModels: () => call<{ models: { name: string }[] }>("/v1/config/embedding-models").then((r) => r.models.map((m) => m.name)),
};

const DEFAULTS = { cutoff: 0.7, most: 3 };

export function SimilarGoalsSettings({ staging: s }: { staging: Staging }) {
  const [models, setModels] = useState<string[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    similarSettingsApi.embeddingModels().then(setModels, (err) => setFailed(err instanceof Error ? err.message : String(err)));
  }, []);
  const config = (s.document.similar_goals ?? {}) as { enabled?: boolean; model?: string; cutoff?: number; most?: number };
  const on = config.enabled !== false;
  const model = String(config.model ?? "");
  // A saved model the list no longer has stays in it, so it shows and can be changed.
  const offered = [...new Set([...(models ?? []), ...(model ? [model] : [])])];
  const gone = models !== null && model !== "" && !models.includes(model);
  return (
    <Stack gap={4}>
      <InlineField
        kind="switch"
        label="New goal shows similar open goals"
        nameByLabel
        value={on}
        switchLabel={on ? "Shown as a description is written" : "Off"}
        changed={changed(s, ["similar_goals", "enabled"])}
        reasons={s.reasons("similar_goals.enabled")}
        readOnly={s.readOnly}
        onCommit={(value) => s.stage(["similar_goals", "enabled"], value)}
      />
      {on && (
        <Group gap="lg" wrap="wrap">
          <InlineField
            kind="select"
            label="Embedding model"
            value={model || null}
            data={offered}
            searchable
            empty="The deployment's own"
            changed={changed(s, ["similar_goals", "model"])}
            reasons={s.reasons("similar_goals.model")}
            readOnly={s.readOnly}
            onCommit={(value) => s.stage(["similar_goals", "model"], value ?? undefined)}
          />
          <InlineField
            kind="number"
            label="How alike a goal must be to show"
            value={Math.round((config.cutoff ?? DEFAULTS.cutoff) * 100)}
            min={0}
            max={100}
            decimalScale={0}
            suffix="% similar or more"
            changed={changed(s, ["similar_goals", "cutoff"])}
            reasons={s.reasons("similar_goals.cutoff")}
            readOnly={s.readOnly}
            onCommit={(value) => s.stage(["similar_goals", "cutoff"], value === null ? undefined : Number(value) / 100)}
          />
          <InlineField
            kind="number"
            label="How many goals show"
            value={config.most ?? DEFAULTS.most}
            min={1}
            max={5}
            decimalScale={0}
            suffix=" at most"
            changed={changed(s, ["similar_goals", "most"])}
            reasons={s.reasons("similar_goals.most")}
            readOnly={s.readOnly}
            onCommit={(value) => s.stage(["similar_goals", "most"], value ?? undefined)}
          />
        </Group>
      )}
      {gone && <Text size="sm" c="orange">{model} is no longer in the list of embedding models. Pick another.</Text>}
      {failed && <Text size="sm" c="dimmed">The embedding models could not be listed ({failed}). Reload the page to try again; the saved model stays.</Text>}
      <Text size="sm" c="dimmed">
        One short embedding call as a description is written. Start low and raise it once the answers on the Performance page show where duplicates begin.
      </Text>
    </Stack>
  );
}
