import { useMemo } from "react";
import {
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MetaResponse, ParetoPoint } from "../lib/api";
import { Failure, Warnings } from "./Kit";
import { BUDGETS, describeDesign, useStore } from "../lib/store";
import { LEVER_NAMES, OBJECTIVE_LABEL, bestPoint } from "./SearchRunner";
import { num } from "../lib/format";

const axis = { fontSize: 11, fill: "var(--color-ink-faint)" };

function objective(point: ParetoPoint, name: string): number | null {
  const value = point.objectives[name];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metric(point: ParetoPoint, name: string): number | null {
  const value = point.metrics?.[name];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function SearchPanel({ meta }: { meta: MetaResponse | undefined }) {
  const store = useStore();
  const { search, searchRequest } = store;
  const result = search.result;
  const best = useMemo(() => bestPoint(result), [result]);
  const site = meta?.sites.find((item) => item.id === store.siteId);

  if (!searchRequest) {
    return (
      <div className="panel space-y-4 p-5">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">No search has run for these inputs</h2>
          <p className="mt-2 text-sm leading-relaxed text-[var(--color-ink-dim)]">
            The other tabs analyse the design exactly as you entered it. A search varies{" "}
            {LEVER_NAMES}. Every candidate is simulated in EnergyPlus against this site's
            weather, must keep night CO₂ under its limit, and is judged on three things: how
            far the occupied night falls below DRDO's 14 °C, the kerosene it burns, and the
            tonnes of material brought in.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {BUDGETS.map((budget) => (
            <button
              key={budget.designs}
              type="button"
              onClick={() => store.requestSearch(budget)}
              className="rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] px-4 py-2 text-left text-sm transition hover:border-[var(--color-solar)]"
            >
              <span className="block font-medium">{budget.label}</span>
              <span className="text-[12px] text-[var(--color-ink-faint)]">
                {budget.designs} designs · about {budget.minutes} min
              </span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  const running = Boolean(search.jobId) || (!result && !search.failure);
  const progress =
    result && result.generations_planned > 0
      ? Math.min(1, result.generation / result.generations_planned)
      : 0;

  const points = (result?.front ?? []).map((point) => ({
    hash: point.design_hash,
    degreeHours: objective(point, "night_degree_hours_below_kh") ?? 0,
    litres: objective(point, "heating_litres") ?? 0,
    tonnes: objective(point, "imported_tonnes") ?? 0,
    feasible: point.feasible,
    knee: point.design_hash === result?.knee_hash,
    shortlisted: (result?.robustness ?? []).some(
      (row) => row.selected && row.design_hash === point.design_hash,
    ),
  }));

  const facts = best
    ? [
        {
          term: "coldest night hour",
          value: num(metric(best, "minimum_overnight_operative_c"), 1),
          unit: "°C",
        },
        {
          term: OBJECTIVE_LABEL.night_degree_hours_below_kh.label,
          value: num(objective(best, "night_degree_hours_below_kh"), 0),
          unit: OBJECTIVE_LABEL.night_degree_hours_below_kh.unit,
        },
        {
          term: OBJECTIVE_LABEL.heating_litres.label,
          value: num(objective(best, "heating_litres"), 1),
          unit: OBJECTIVE_LABEL.heating_litres.unit,
        },
        {
          term: OBJECTIVE_LABEL.imported_tonnes.label,
          value: num(objective(best, "imported_tonnes"), 2),
          unit: OBJECTIVE_LABEL.imported_tonnes.unit,
        },
        {
          term: "highest night CO₂",
          value: num(metric(best, "peak_night_co2_ppm"), 0),
          unit: `ppm, limit ${num(metric(best, "co2_limit_ppm"), 0)}`,
        },
      ]
    : [];

  const frontByHash = new Map((result?.front ?? []).map((point) => [point.design_hash, point]));

  return (
    <div className="space-y-6">
      <div className="panel space-y-3 p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-lg font-semibold tracking-tight">
            {running ? "Searching" : search.failure ? "The search stopped" : "Recommended"}
          </h2>
          <p className="tnum text-[13px] text-[var(--color-ink-faint)]">
            {searchRequest.budget.label} · {searchRequest.budget.designs} designs · about{" "}
            {searchRequest.budget.minutes} min
          </p>
        </div>
        {running && (
          <div className="space-y-2">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-line-soft)]">
              <div
                className={`h-full rounded-full bg-[var(--color-solar)] transition-all ${result ? "" : "w-1/4 animate-pulse"}`}
                style={result ? { width: `${Math.max(4, progress * 100)}%` } : undefined}
              />
            </div>
            <p className="text-[13px] text-[var(--color-ink-dim)]">
              {result
                ? `Generation ${result.generation} of ${result.generations_planned} · ${result.evaluated} designs simulated. The 3D view switches to the best design each time the search finds a better one.`
                : "The first generation is running. Results appear as soon as the first designs finish."}
            </p>
          </div>
        )}
        {!running && result && (
          <p className="tnum text-[13px] text-[var(--color-ink-faint)]">
            {result.evaluated} designs simulated in {num(result.runtime_s, 0)} s
            {site ? ` at ${site.name}` : ""}
          </p>
        )}
        {search.failure && <Failure error={search.failure} />}
      </div>

      {best && (
        <div className="panel space-y-5 p-5">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-ink-faint)]">
              {running ? "Best so far" : "Recommendation"}
            </p>
            <p className="mt-1 text-xl font-semibold tracking-tight">{describeDesign(best.design)}</p>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-ink-dim)]">
              Of the designs that kept night CO₂ under the limit, this one bulges furthest
              toward the ideal corner from the line joining the best design on each measure.
              The others on the chart are all defensible; choosing between them is a judgement
              about fuel against airlift.
            </p>
          </div>
          <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
            {facts.map((item) => (
              <div key={item.term}>
                <dt className="text-[13px] text-[var(--color-ink-faint)]">{item.term}</dt>
                <dd className="tnum mt-1 text-lg font-semibold">
                  {item.value}
                  <span className="ml-1 text-sm font-normal text-[var(--color-ink-dim)]">
                    {item.unit}
                  </span>
                </dd>
              </div>
            ))}
          </dl>
          {best.flags.length > 0 && (
            <ul className="space-y-1 text-[13px] text-[var(--color-ink-dim)]">
              {best.flags.map((flag) => (
                <li key={flag}>· {flag.replace(/_/g, " ")}</li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => {
                store.applyOverride(best.design);
                store.open("3d model");
              }}
              className="rounded-lg bg-[var(--color-solar)] px-4 py-2 text-sm font-medium text-white shadow-sm"
            >
              Open this design in 3D
            </button>
            <button
              type="button"
              onClick={() => {
                store.applyOverride(best.design);
                store.open("performance");
              }}
              className="rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] px-4 py-2 text-sm"
            >
              Full performance for this design
            </button>
          </div>
        </div>
      )}

      {(result?.robustness ?? []).length > 0 && (
        <div className="panel space-y-3 p-5">
          <div>
            <h3 className="text-base font-semibold tracking-tight">Shortlist for ANSYS</h3>
            <p className="mt-1 text-sm leading-relaxed text-[var(--color-ink-dim)]">
              The five best by the knee rule, each re-run at every corner of its uncertain
              inputs. Ranked by the worst corner, because a design that is best on paper and
              fragile in the field is the wrong one to confirm. The top three go on to ANSYS.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="text-[11px] uppercase tracking-wide text-[var(--color-ink-faint)]">
                <tr>
                  <th className="py-1 pr-3 font-medium">Robust</th>
                  <th className="py-1 pr-3 font-medium">Design</th>
                  <th className="py-1 pr-3 font-medium">Nominal</th>
                  <th className="py-1 pr-3 font-medium">Across corners</th>
                  <th className="py-1 pr-3 font-medium">Driven by</th>
                  <th className="py-1 font-medium" />
                </tr>
              </thead>
              <tbody>
                {[...(result?.robustness ?? [])]
                  .sort((a, b) => a.robust_rank - b.robust_rank)
                  .map((row) => {
                    const point = frontByHash.get(row.design_hash);
                    return (
                      <tr key={row.design_hash} className="border-t border-[var(--color-line-soft)]">
                        <td className="tnum py-2 pr-3 font-semibold">
                          #{row.robust_rank}
                          {row.selected && (
                            <span className="ml-2 rounded bg-[var(--color-good)]/15 px-1.5 py-0.5 text-[11px] font-medium text-[var(--color-good)]">
                              to ANSYS
                            </span>
                          )}
                        </td>
                        <td className="py-2 pr-3 text-[var(--color-ink-dim)]">
                          {point ? describeDesign(point.design) : row.design_hash.slice(0, 8)}
                          <span className="ml-1 text-[var(--color-ink-faint)]">
                            (knee #{row.knee_rank})
                          </span>
                        </td>
                        <td className="tnum py-2 pr-3">
                          {num(row.nominal, 0)} {row.unit}
                        </td>
                        <td className="tnum py-2 pr-3">
                          {num(row.best, 0)} to {num(row.worst, 0)} {row.unit}
                        </td>
                        <td className="py-2 pr-3 text-[var(--color-ink-faint)]">
                          {row.dominant_parameter ?? "no uncertain inputs"}
                        </td>
                        <td className="py-2 text-right">
                          {point && (
                            <button
                              type="button"
                              onClick={() => {
                                store.applyOverride(point.design);
                                store.open("3d model");
                              }}
                              className="rounded-md border border-[var(--color-line)] px-2 py-1 text-[12px] hover:border-[var(--color-solar)]"
                            >
                              Open
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {points.length > 0 && (
        <div className="panel space-y-3 p-5">
          <p className="text-sm text-[var(--color-ink-dim)]">
            Every design the search kept. Left is warmer at night, down burns less kerosene,
            and larger points bring in more material. The highlighted point is the
            recommendation; outlined points are the ANSYS shortlist.
          </p>
          <div className="h-[300px]">
            <ResponsiveContainer>
              <ScatterChart margin={{ top: 8, right: 12, bottom: 28, left: 8 }}>
                <CartesianGrid stroke="var(--color-line-soft)" />
                <XAxis
                  type="number"
                  dataKey="degreeHours"
                  name="night degree hours"
                  tick={axis}
                  stroke="var(--color-line)"
                  label={{
                    value: "night degree hours below 14 °C (Kh)",
                    position: "insideBottom",
                    offset: -16,
                    ...axis,
                  }}
                />
                <YAxis
                  type="number"
                  dataKey="litres"
                  name="kerosene"
                  tick={axis}
                  stroke="var(--color-line)"
                  width={56}
                  label={{ value: "litres", angle: -90, position: "insideLeft", ...axis }}
                />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  contentStyle={{
                    background: "var(--color-day)",
                    color: "var(--color-ink)",
                    border: "1px solid var(--color-line)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: number, key: string) =>
                    key === "kerosene" ? `${num(value, 1)} L` : `${num(value, 0)} Kh`
                  }
                />
                <Scatter data={points}>
                  {points.map((point) => (
                    <Cell
                      key={point.hash}
                      fill={
                        point.knee
                          ? "var(--color-solar)"
                          : point.feasible
                            ? "var(--color-cold)"
                            : "var(--color-diesel)"
                      }
                      stroke={point.shortlisted ? "var(--color-good)" : undefined}
                      strokeWidth={point.shortlisted ? 2.5 : 0}
                      r={point.knee ? 8 : 3 + Math.min(6, point.tonnes)}
                    />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <p className="text-[13px] leading-relaxed text-[var(--color-ink-faint)]">
            Red points broke a limit, usually night CO₂, and are shown rather than hidden.
            Rupees are left out until they come from the UT Ladakh schedule of rates; litres
            and tonnes are what a post and an airlift actually count.
          </p>
        </div>
      )}

      {result && result.warnings.length > 0 && <Warnings warnings={result.warnings} />}
    </div>
  );
}
