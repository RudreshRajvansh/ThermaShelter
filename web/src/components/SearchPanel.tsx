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
import type { MetaResponse } from "../lib/api";
import { Failure, Warnings } from "./Kit";
import { BUDGETS, describeDesign, useStore } from "../lib/store";
import { LEVER_NAMES, bestPoint } from "./SearchRunner";
import { num, rupees } from "../lib/format";

const axis = { fontSize: 11, fill: "var(--color-ink-faint)" };

export function SearchPanel({ meta }: { meta: MetaResponse | undefined }) {
  const store = useStore();
  const { search, searchRequest } = store;
  const result = search.result;
  const best = useMemo(() => bestPoint(result), [result]);
  const site = meta?.sites.find((item) => item.id === store.siteId);
  const headline = site?.headline_metric ?? "minimum_overnight_operative_c";

  if (!searchRequest) {
    return (
      <div className="panel space-y-4 p-5">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">No search has run for these inputs</h2>
          <p className="mt-2 text-sm leading-relaxed text-[var(--color-ink-dim)]">
            The other tabs analyse the design exactly as you entered it. A search varies{" "}
            {LEVER_NAMES}, simulating every candidate in EnergyPlus against this site's weather
            year, and recommends the best balance of warmth, cost and the worst design night.
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
    degreeHours: point.objectives.degree_hours_total_kh ?? 0,
    cost: point.objectives.total_cost_inr ?? 0,
    feasible: point.feasible,
    knee: point.design_hash === result?.knee_hash,
  }));

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
              Of the designs simulated, this one sits closest to the ideal corner once warmth,
              cost and the worst design night are weighed together. The others on the chart are
              all defensible; choosing between them is a judgement about budget and buildability.
            </p>
          </div>
          <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-3">
            {[
              {
                term: headline.replace(/_/g, " "),
                value: num(best.metrics?.minimum_overnight_operative_c, 1),
                unit: "°C",
              },
              {
                term: "hours outside the comfort band",
                value: num(best.metrics?.hours_outside_band, 0),
                unit: "h",
              },
              {
                term: "delivered material cost",
                value: rupees(best.objectives.total_cost_inr ?? 0),
                unit: "",
              },
            ].map((item) => (
              <div key={item.term}>
                <dt className="text-[13px] text-[var(--color-ink-faint)]">{item.term}</dt>
                <dd className="tnum mt-1 text-lg font-semibold">
                  {item.value}
                  {item.unit && (
                    <span className="ml-1 text-sm font-normal text-[var(--color-ink-dim)]">
                      {item.unit}
                    </span>
                  )}
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

      {points.length > 0 && (
        <div className="panel space-y-3 p-5">
          <p className="text-sm text-[var(--color-ink-dim)]">
            Every design the search kept. Left is warmer, down is cheaper, and the highlighted
            point is the recommendation.
          </p>
          <div className="h-[300px]">
            <ResponsiveContainer>
              <ScatterChart margin={{ top: 8, right: 12, bottom: 28, left: 8 }}>
                <CartesianGrid stroke="var(--color-line-soft)" />
                <XAxis
                  type="number"
                  dataKey="degreeHours"
                  name="degree hours"
                  tick={axis}
                  stroke="var(--color-line)"
                  label={{
                    value: "degree hours outside the band (Kh)",
                    position: "insideBottom",
                    offset: -16,
                    ...axis,
                  }}
                />
                <YAxis type="number" dataKey="cost" name="cost" tick={axis} stroke="var(--color-line)" width={72} />
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
                    key === "cost" ? rupees(value) : num(value, 0)
                  }
                />
                <Scatter data={points}>
                  {points.map((point, index) => (
                    <Cell
                      key={index}
                      fill={
                        point.knee
                          ? "var(--color-solar)"
                          : point.feasible
                            ? "var(--color-cold)"
                            : "var(--color-diesel)"
                      }
                      r={point.knee ? 7 : 4}
                    />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <p className="text-[13px] leading-relaxed text-[var(--color-ink-faint)]">
            Red points broke a constraint and are shown rather than hidden. Rupee figures come
            from delivered material rates that carry no verified source yet, so read the cost
            axis as relative.
          </p>
        </div>
      )}

      {result && result.warnings.length > 0 && <Warnings warnings={result.warnings} />}
    </div>
  );
}
