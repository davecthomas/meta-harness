import { useState } from "react";
import { useElementSize } from "@mantine/hooks";
import type { Costs } from "./api";

// Cost over time: one stacked column per bucket, a segment per series, and the trend line through the totals.
// One axis. Thin columns with a gap in the surface color between segments, rounded at the top; a recessive
// grid; a tooltip for the hovered bucket that lists every series. The legend and the breakdown list beside the
// chart carry the same figures as text, so nothing is read from color alone.

// The categorical palette, in its fixed order, for the light and the dark surface. Validated as a set
// (colorblind separation, contrast) in both modes. A series' color is its slot, never its rank.
export const SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
export const SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
// What is folded together has no identity: a neutral.
export const OTHER_COLOR = "var(--mantine-color-gray-5)";

const slots = (colors: string[]) => colors.map((c, i) => `--series-${i + 1}: ${c};`).join(" ");
export const CHART_CSS = `.cost-viz { ${slots(SERIES_LIGHT)} } [data-mantine-color-scheme="dark"] .cost-viz { ${slots(SERIES_DARK)} }`;

export const usd = (n: number) => `$${n.toFixed(2)}`;

// A series' color: its slot among every value its dimension takes, so a filter never repaints the ones left.
export function seriesColor(key: string, order: string[]): string {
  if (key === "other") return OTHER_COLOR;
  const slot = order.indexOf(key);
  return `var(--series-${((slot < 0 ? 0 : slot) % 8) + 1})`;
}

// Round axis steps: 1, 2 or 5 times a power of ten, about four of them up to the largest total.
export function axisTicks(max: number): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? 10 * power;
  const ticks = [];
  for (let t = 0; t < max + step - 1e-9; t += step) ticks.push(Number(t.toFixed(6)));
  return ticks;
}

export function bucketLabel(iso: string, bucket: string): string {
  const day = new Date(`${iso}T00:00:00`);
  if (bucket === "month") return day.toLocaleDateString("en-US", { month: "short", year: "numeric" });
  return day.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const HEIGHT = 300;
const PAD = { top: 12, right: 12, bottom: 28, left: 52 };
const GAP = 2;

export function CostChart({ costs, order, label }: { costs: Costs; order: string[]; label: (key: string) => string }) {
  const { ref, width: measured } = useElementSize();
  const [hover, setHover] = useState<number | null>(null);
  const width = measured || 800;
  const n = costs.buckets.length;
  const plotW = Math.max(width - PAD.left - PAD.right, 10);
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const peak = Math.max(...costs.totals, costs.trend ? Math.max(costs.trend.start, costs.trend.end) : 0, 0);
  const ticks = axisTicks(peak);
  const top = ticks[ticks.length - 1];
  const y = (v: number) => PAD.top + plotH - (Math.max(v, 0) / top) * plotH;
  const band = plotW / Math.max(n, 1);
  const bar = Math.max(Math.min(band - 4, 28), 2);
  const x = (i: number) => PAD.left + band * i + band / 2;
  // About eight date labels, however many buckets.
  const every = Math.max(1, Math.ceil(n / 8));
  const rows = hover === null ? [] : costs.series.filter((s) => s.usd[hover] > 0);
  const tipLeft = hover === null ? 0 : Math.min(Math.max(x(hover) + 12, 0), Math.max(width - 230, 0));

  return (
    <div ref={ref} className="cost-viz" style={{ position: "relative" }} data-testid="cost-chart">
      <style>{CHART_CSS}</style>
      <svg width={width} height={HEIGHT} role="img" aria-label={`Cost per ${costs.bucket} from ${costs.from} to ${costs.to}, ${usd(costs.total)} in all`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--mantine-color-default-border)" strokeWidth={1} opacity={t === 0 ? 1 : 0.5} />
            <text x={PAD.left - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--mantine-color-dimmed)">
              ${top >= 10 ? t.toFixed(0) : t.toFixed(2)}
            </text>
          </g>
        ))}
        {costs.buckets.map((b, i) => {
          let base = 0;
          const shown = costs.series.filter((s) => s.usd[i] > 0);
          return (
            <g key={b} opacity={hover === null || hover === i ? 1 : 0.55}>
              {shown.map((s, at) => {
                const from = y(base);
                base += s.usd[i];
                const to = y(base);
                // A gap in the surface color between segments; the top segment's free end is rounded.
                const last = at === shown.length - 1;
                const h = Math.max(from - to - (last ? 0 : GAP), 1);
                const r = last ? Math.min(4, h, bar / 2) : 0;
                const left = x(i) - bar / 2;
                const d = `M${left},${to + h} V${to + r} Q${left},${to} ${left + r},${to} H${left + bar - r} Q${left + bar},${to} ${left + bar},${to + r} V${to + h} Z`;
                return <path key={s.key} d={d} fill={seriesColor(s.key, order)} data-series={s.key} />;
              })}
              {i % every === 0 && (
                <text x={x(i)} y={HEIGHT - 8} textAnchor="middle" fontSize={11} fill="var(--mantine-color-dimmed)">{bucketLabel(b, costs.bucket)}</text>
              )}
            </g>
          );
        })}
        {costs.trend && n > 1 && (
          <line
            x1={x(0)}
            x2={x(n - 1)}
            y1={y(costs.trend.start)}
            y2={y(costs.trend.end)}
            stroke="var(--mantine-color-text)"
            strokeWidth={2}
            strokeDasharray="6 5"
            strokeLinecap="round"
            data-testid="trend-line"
          />
        )}
        {/* The whole column is the hit target, wider than its bar. */}
        {costs.buckets.map((b, i) => (
          <rect
            key={b}
            x={PAD.left + band * i}
            y={PAD.top}
            width={band}
            height={plotH}
            fill="transparent"
            tabIndex={0}
            aria-label={`${bucketLabel(b, costs.bucket)}: ${usd(costs.totals[i])}`}
            onPointerEnter={() => setHover(i)}
            onPointerLeave={() => setHover((now) => (now === i ? null : now))}
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
          />
        ))}
      </svg>
      {hover !== null && (
        <div
          role="tooltip"
          style={{
            position: "absolute",
            top: PAD.top,
            left: tipLeft,
            width: 218,
            pointerEvents: "none",
            padding: "8px 10px",
            borderRadius: 8,
            background: "var(--mantine-color-body)",
            border: "1px solid var(--mantine-color-default-border)",
            boxShadow: "var(--mantine-shadow-md)",
            fontSize: 12,
          }}
        >
          <div style={{ color: "var(--mantine-color-dimmed)", marginBottom: 4 }}>{bucketLabel(costs.buckets[hover], costs.bucket)}</div>
          {rows.map((s) => (
            <div key={s.key} style={{ display: "flex", alignItems: "center", gap: 6, lineHeight: 1.6 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: seriesColor(s.key, order), flexShrink: 0 }} />
              <strong>{usd(s.usd[hover])}</strong>
              <span style={{ color: "var(--mantine-color-dimmed)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label(s.key)}</span>
            </div>
          ))}
          {rows.length === 0 && <span style={{ color: "var(--mantine-color-dimmed)" }}>Nothing spent</span>}
          {/* The stack's total, under its parts. */}
          {rows.length > 0 && (
            <div style={{ display: "flex", gap: 6, borderTop: "1px solid var(--mantine-color-default-border)", marginTop: 4, paddingTop: 4, paddingLeft: 14 }}>
              <strong>{usd(costs.totals[hover])}</strong>
              <span style={{ color: "var(--mantine-color-dimmed)" }}>Total</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
