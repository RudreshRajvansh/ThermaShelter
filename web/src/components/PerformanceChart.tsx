import { useMemo } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SimulationResult } from "../lib/api";
import { seriesByName } from "../lib/api";
import { num } from "../lib/format";

type Row = {
  t: string;
  label: string;
  operative: number | null;
  outdoor: number | null;
  reference: number | null;
  band: [number, number] | null;
  lower: number | null;
};

function tickLabel(iso: string): string {
  return `${iso.slice(5, 10)} ${iso.slice(11, 13)}:00`;
}

function tickDisplay(label: string): string {
  return label.endsWith("00:00") ? label.slice(0, 5) : label.slice(6);
}

function tickIndices(stamps: string[]): string[] {
  if (stamps.length === 0) return [];
  const span = stamps.length;
  const stride = span > 24 * 8 ? 48 : span > 24 * 3 ? 24 : span > 24 ? 12 : 6;
  const ticks: string[] = [];
  for (let index = 0; index < span; index += 1) {
    const hour = Number(stamps[index].slice(11, 13));
    if (hour % stride === 0) ticks.push(stamps[index]);
  }
  return ticks;
}

export function PerformanceChart({ result }: { result: SimulationResult }) {
  const rows = useMemo<Row[]>(() => {
    const operative = seriesByName(result, "operative_temperature_c");
    const outdoor = seriesByName(result, "outdoor_temperature_c");
    const lower = seriesByName(result, "comfort_lower_bound_c");
    const upper = seriesByName(result, "comfort_upper_bound_c");
    const reference = seriesByName(result, "reference_operative_temperature_c");
    if (!operative) return [];
    const referenceByStamp = new Map<string, number | null>();
    reference?.timestamps.forEach((stamp, index) => {
      referenceByStamp.set(stamp, reference.values[index] ?? null);
    });
    return operative.timestamps.map((stamp, index) => {
      const low = lower?.values[index] ?? null;
      const high = upper?.values[index] ?? null;
      return {
        t: stamp,
        label: tickLabel(stamp),
        operative: operative.values[index] ?? null,
        outdoor: outdoor?.values[index] ?? null,
        reference: referenceByStamp.get(stamp) ?? null,
        band: low !== null && high !== null ? [low, high] : null,
        lower: low,
      };
    });
  }, [result]);

  const comfortModel = result.comfort.comfort_model_id;

  if (rows.length === 0) {
    return (
      <p className="text-sm text-[var(--color-ink-faint)]">
        This run produced no hourly series to plot.
      </p>
    );
  }

  const ticks = tickIndices(rows.map((row) => row.t)).map(tickLabel);

  return (
    <div className="h-[340px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#22334c" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            ticks={ticks}
            interval={0}
            tickFormatter={tickDisplay}
            tick={{ fontSize: 11, fill: "var(--color-ink-faint)" }}
            stroke="#22334c"
          />
          <YAxis
            unit="°"
            width={52}
            tick={{ fontSize: 11, fill: "var(--color-ink-faint)" }}
            stroke="#22334c"
          />
          <Tooltip
            contentStyle={{
              fontSize: 12,
              borderRadius: 8,
              background: "#0b1220",
              border: "1px solid #22334c",
            }}
            labelFormatter={(_label, payload) => payload?.[0]?.payload?.t ?? ""}
            formatter={(value: unknown, name: string) => {
              if (Array.isArray(value)) {
                return [`${num(value[0])} to ${num(value[1])} °C`, name];
              }
              return [`${num(value as number)} °C`, name];
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Area
            dataKey="band"
            name={`Comfort band (${comfortModel})`}
            stroke="none"
            fill="var(--color-good)"
            fillOpacity={0.14}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            dataKey="outdoor"
            name="Outdoor air"
            stroke="var(--color-cold)"
            strokeWidth={1}
            strokeDasharray="4 3"
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            dataKey="reference"
            name="Current practice"
            stroke="var(--color-diesel)"
            strokeWidth={1.5}
            strokeOpacity={0.9}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            dataKey="operative"
            name="This design, operative"
            stroke="var(--color-solar)"
            strokeWidth={2.25}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          {result.comfort.minimum_overnight_operative_c !== null &&
            result.comfort.minimum_overnight_operative_c !== undefined && (
              <ReferenceLine
                y={result.comfort.minimum_overnight_operative_c}
                stroke="var(--color-diesel)"
                strokeDasharray="2 4"
                label={{
                  value: `overnight min ${num(result.comfort.minimum_overnight_operative_c)}°`,
                  position: "insideTopLeft",
                  fontSize: 11,
                  fill: "var(--color-diesel)",
                }}
              />
            )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
