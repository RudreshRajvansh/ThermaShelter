import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, seriesByName, type DesignSpec, type SimulationResult } from "../lib/api";
import { Failure, Loading, Panel, Stat } from "../components/Kit";
import { useStore } from "../lib/store";
import { num, rupees, signed } from "../lib/format";

const axis = { fontSize: 11, fill: "var(--color-ink-faint)" };
const grid = "#22334c";
const COLOURS = ["var(--color-solar)", "var(--color-cold)", "var(--color-good)"];

export function Compare() {
  const store = useStore();
  const [selected, setSelected] = useState<string[]>([
    "candidate_single_room",
    "candidate_sunspace",
  ]);

  const catalogue = useQuery({
    queryKey: ["design-catalogue"],
    queryFn: async (): Promise<Record<string, DesignSpec>> => {
      const files = ["single_room", "sunspace", "trombe_wall", "vestibule"];
      const entries = await Promise.all(
        files.map(async (name) => {
          const response = await fetch(`/designs/${name}.json`);
          if (!response.ok) return null;
          return [`candidate_${name}`, (await response.json()) as DesignSpec] as const;
        }),
      );
      return Object.fromEntries(entries.filter(Boolean) as [string, DesignSpec][]);
    },
    staleTime: Infinity,
  });

  const designs = selected
    .map((name) => catalogue.data?.[name])
    .filter(Boolean) as DesignSpec[];

  const table = useQuery({
    queryKey: ["compare", selected.join("|"), store.siteId, store.period.start],
    queryFn: () =>
      api.compare({
        designs,
        site_id: store.siteId,
        period: store.period,
        conditioned_run: true,
      }),
    enabled: designs.length > 0,
    staleTime: Infinity,
  });

  const curves = useQuery({
    queryKey: ["compare-curves", selected.join("|"), store.siteId, store.period.start],
    queryFn: async () => {
      const results: SimulationResult[] = [];
      for (const design of designs) {
        results.push(
          await api.simulate({
            design,
            site_id: store.siteId,
            period: store.period,
            conditioned_run: false,
            surface_output: false,
            with_uncertainty: false,
            with_reference: true,
          }),
        );
      }
      return results;
    },
    enabled: designs.length > 0,
    staleTime: Infinity,
  });

  const chartRows = (() => {
    const results = curves.data ?? [];
    if (results.length === 0) return [];
    const base = seriesByName(results[0], "operative_temperature_c");
    const reference = seriesByName(results[0], "reference_operative_temperature_c");
    const outdoor = seriesByName(results[0], "outdoor_temperature_c");
    if (!base) return [];
    return base.timestamps.map((stamp, index) => {
      const row: Record<string, string | number | null> = {
        label: `${stamp.slice(5, 10)} ${stamp.slice(11, 13)}h`,
        outdoor: outdoor?.values[index] ?? null,
        reference: reference?.values[index] ?? null,
      };
      results.forEach((result, position) => {
        const series = seriesByName(result, "operative_temperature_c");
        row[`d${position}`] = series?.values[index] ?? null;
      });
      return row;
    });
  })();

  const ticks = chartRows
    .filter((row) => String(row.label).endsWith("00h"))
    .map((row) => String(row.label));

  return (
    <div className="space-y-4">
      <Panel
        title="Choose the designs to put side by side"
        subtitle="Every design runs against the same weather, the same period and the same occupancy, and every row is differenced against current construction."
      >
        <div className="flex flex-wrap gap-2">
          {Object.keys(catalogue.data ?? {}).map((name) => {
            const active = selected.includes(name);
            return (
              <button
                key={name}
                type="button"
                onClick={() =>
                  setSelected((current) =>
                    current.includes(name)
                      ? current.filter((item) => item !== name)
                      : current.length >= 3
                        ? current
                        : [...current, name],
                  )
                }
                className={`rounded-md border px-2.5 py-1 text-xs capitalize ${
                  active
                    ? "border-[var(--color-solar)] bg-[var(--color-solar)]/15 text-[var(--color-solar)]"
                    : "border-[var(--color-line)] text-[var(--color-ink-dim)]"
                }`}
              >
                {name.replace(/_/g, " ")}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] text-[var(--color-ink-faint)]">
          Up to three at a time. Each one is a full EnergyPlus run, so the first request
          takes a few seconds per design and repeats come from cache.
        </p>
      </Panel>

      <Panel
        title="Temperature curves"
        subtitle="The reference line is current construction. If a candidate does not separate from it after sunset, it has not done anything."
      >
        {curves.isError ? (
          <Failure error={curves.error} />
        ) : curves.isPending ? (
          <Loading label="simulating each design…" />
        ) : (
          <div className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartRows} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
                <CartesianGrid stroke={grid} strokeDasharray="3 3" />
                <XAxis
                  dataKey="label"
                  ticks={ticks}
                  interval={0}
                  tick={axis}
                  stroke={grid}
                  tickFormatter={(value: string) => value.slice(0, 5)}
                />
                <YAxis unit="°" tick={axis} stroke={grid} width={46} />
                <Tooltip
                  contentStyle={{
                    background: "#0b1220",
                    border: "1px solid #22334c",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Line
                  dataKey="outdoor"
                  name="outdoor air"
                  stroke="var(--color-cold)"
                  strokeWidth={1}
                  strokeDasharray="4 3"
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  dataKey="reference"
                  name="current practice"
                  stroke="var(--color-diesel)"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
                {(curves.data ?? []).map((result, index) => (
                  <Line
                    key={result.design_hash}
                    dataKey={`d${index}`}
                    name={result.design.name.replace(/_/g, " ")}
                    stroke={COLOURS[index % COLOURS.length]}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>

      <Panel
        title="Design ranking"
        subtitle="Named metrics against a named baseline. No composite score out of a hundred, because there is no defensible answer to where the weights came from."
      >
        {table.isError ? (
          <Failure error={table.error} />
        ) : !table.data ? (
          <Loading label="comparing…" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[var(--color-ink-faint)]">
                <tr>
                  <th className="py-1 pr-3 font-medium">Design</th>
                  <th className="py-1 pr-3 text-right font-medium">Overnight min °C</th>
                  <th className="py-1 pr-3 text-right font-medium">Hours below</th>
                  <th className="py-1 pr-3 text-right font-medium">Degree hours Kh</th>
                  <th className="py-1 pr-3 text-right font-medium">Heating kWh</th>
                  <th className="py-1 pr-3 text-right font-medium">Fuel</th>
                  <th className="py-1 pr-3 text-right font-medium">Cost</th>
                  <th className="py-1 pr-3 text-right font-medium">Wall m</th>
                  <th className="py-1 pr-3 font-medium">Labour</th>
                  <th className="py-1 text-right font-medium">Condensation h</th>
                </tr>
              </thead>
              <tbody>
                {table.data.rows.map((row) => (
                  <tr
                    key={row.design_hash}
                    className={`border-t border-[var(--color-line-soft)] ${
                      row.is_reference ? "text-[var(--color-diesel)]" : ""
                    }`}
                  >
                    <td className="py-1.5 pr-3 capitalize">
                      {row.name.replace(/_/g, " ")}
                      {row.is_reference && (
                        <span className="ml-2 rounded bg-[var(--color-diesel)]/20 px-1.5 py-0.5 text-[10px]">
                          reference
                        </span>
                      )}
                    </td>
                    <td className="tnum py-1.5 pr-3 text-right">
                      {num(row.minimum_overnight_operative_c)}
                      {row.minimum_overnight_band && (
                        <span className="block text-[10px] text-[var(--color-ink-faint)]">
                          {num(row.minimum_overnight_band[0])} to{" "}
                          {num(row.minimum_overnight_band[1])}
                        </span>
                      )}
                    </td>
                    <td className="tnum py-1.5 pr-3 text-right">
                      {num(row.hours_below_band, 0)}
                    </td>
                    <td className="tnum py-1.5 pr-3 text-right">
                      {num(row.degree_hours_total_kh, 0)}
                      {row.delta_vs_reference?.degree_hours_total_kh !== undefined && (
                        <span className="block text-[10px] text-[var(--color-good)]">
                          {signed(row.delta_vs_reference.degree_hours_total_kh, 0)}
                        </span>
                      )}
                    </td>
                    <td className="tnum py-1.5 pr-3 text-right">
                      {num(row.heating_demand_kwh, 0)}
                    </td>
                    <td className="tnum py-1.5 pr-3 text-right">
                      {num(row.fuel_units, 0)}
                    </td>
                    <td className="tnum py-1.5 pr-3 text-right">
                      {rupees(row.total_cost_inr)}
                    </td>
                    <td className="tnum py-1.5 pr-3 text-right">
                      {num(row.wall_thickness_m, 2)}
                    </td>
                    <td className="py-1.5 pr-3">{row.mason_skill ?? "—"}</td>
                    <td className="tnum py-1.5 text-right">
                      {num(row.condensation_hours, 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {curves.data && curves.data.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {curves.data.map((result, index) => (
            <Stat
              key={result.design_hash}
              label={result.design.name.replace(/_/g, " ")}
              value={num(result.comfort.minimum_overnight_operative_c)}
              unit="°C overnight"
              tone={index === 0 ? "solar" : "neutral"}
              note={`${num(result.comfort.hours_below_band, 0)} h below the band · ${rupees(
                result.cost.total_inr,
              )}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
