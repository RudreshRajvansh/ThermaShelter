import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { useMutation } from "@tanstack/react-query";
import {
  api,
  type DesignVariable,
  type ParetoPoint,
  type ParetoResult,
} from "../lib/api";
import { Empty, Loading, Stat } from "./Kit";
import { activeDesign, useStore } from "../lib/store";
import { num, rupees } from "../lib/format";

const axis = { fontSize: 11, fill: "var(--color-ink-faint)" };
const grid = "#22334c";

function variablesFor(): DesignVariable[] {
  return [
    {
      name: "wall.layers.2.thickness_m",
      kind: "real",
      low: 0.15,
      high: 0.55,
      options: null,
    },
    {
      name: "wall.layers.1.thickness_m",
      kind: "real",
      low: 0.02,
      high: 0.18,
      options: null,
    },
    {
      name: "windows.south.wwr",
      kind: "real",
      low: 0.08,
      high: 0.45,
      options: null,
    },
  ];
}

export function ParetoExplorer() {
  const store = useStore();
  const design = activeDesign(store);
  const [result, setResult] = useState<ParetoResult | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<string>("");
  const [populationSize, setPopulationSize] = useState(20);
  const [generations, setGenerations] = useState(5);
  const timer = useRef<number | null>(null);

  const budget = populationSize * generations;

  const start = useMutation({
    mutationFn: async () => {
      if (!design) throw new Error("no design loaded");
      return api.optimise({
        base_design: design,
        variables: variablesFor(),
        objectives: ["degree_hours_total_kh", "total_cost_inr", "worst_night_margin_k"],
        constraints: { max_condensation_hours: 2000, max_wall_thickness_m: 0.9 },
        population_size: populationSize,
        generations: generations,
        seed: 1,
        site_id: store.siteId,
        period: { start: store.period.start, end: store.period.start },
      });
    },
    onSuccess: (job) => {
      setJobId(job.job_id);
      setProgress("running");
    },
  });

  useEffect(() => {
    if (!jobId) return;
    const poll = async () => {
      const job = await api.optimisationStatus(jobId);
      if (job.result) {
        setResult(job.result as unknown as ParetoResult);
      }
      if (job.status === "done") {
        setProgress("done");
        setJobId(null);
      } else if (job.status === "failed") {
        setProgress(job.message ?? "the search failed");
        setJobId(null);
      }
    };
    timer.current = window.setInterval(poll, 1500);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [jobId]);

  const points = (result?.front ?? []).map((point: ParetoPoint) => ({
    degreeHours: point.objectives.degree_hours_total_kh ?? 0,
    cost: point.objectives.total_cost_inr ?? 0,
    margin: point.objectives.worst_night_margin_k ?? 0,
    hash: point.design_hash,
    feasible: point.feasible,
    wall: point.design.wall.layers.reduce(
      (sum: number, layer: { thickness_m: number }) => sum + layer.thickness_m,
      0,
    ),
    knee: point.design_hash === result?.knee_hash,
  }));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => start.mutate()}
          disabled={start.isPending || Boolean(jobId) || !design}
          className="rounded-md bg-[var(--color-solar)] px-3 py-1.5 text-xs font-medium text-[#0b1220] disabled:opacity-40"
        >
          {jobId ? "searching…" : `Search ${budget} designs`}
        </button>
        <label className="flex items-center gap-1.5 text-[11px] text-[var(--color-ink-faint)]">
          budget
          <select
            value={budget}
            disabled={Boolean(jobId)}
            onChange={(event) => {
              const chosen = Number(event.target.value);
              const size = chosen <= 100 ? 20 : chosen <= 500 ? 25 : 50;
              setPopulationSize(size);
              setGenerations(Math.max(2, Math.round(chosen / size)));
            }}
            className="rounded border border-[var(--color-line)] bg-white px-1.5 py-0.5 text-[11px]"
          >
            {[100, 300, 500, 900, 1000].map((item) => (
              <option key={item} value={item}>
                {item} designs
              </option>
            ))}
          </select>
        </label>
        {result && !result.complete && (
          <span className="rounded bg-[var(--color-solar)]/15 px-2 py-0.5 text-[11px] text-[var(--color-solar)]">
            generation {result.generation} of {result.generations_planned} ·{" "}
            {result.evaluated} simulated so far
          </span>
        )}
        <span className="text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
          Wall mass, insulation thickness and south glazing varied together against three
          goals at once. The front below is redrawn after every generation, so the best
          design so far is on screen while the search is still running. Each dot is one
          full EnergyPlus run, so a larger budget costs real time.
        </span>
      </div>

      {start.isError && (
        <div className="text-xs text-[#ffb4ae]">{String(start.error)}</div>
      )}
      {jobId && <Loading label="the search is running as a background job…" />}
      {progress && progress !== "running" && progress !== "done" && (
        <div className="text-xs text-[#ffb4ae]">{progress}</div>
      )}

      {!result && !jobId && (
        <Empty
          title="No design front has been computed in this session"
          detail="Every point on a Pareto front is one full EnergyPlus run, so the front is a batch job rather than something computed while you wait. Run a small search here, or load a stored front produced offline."
        />
      )}

      {result && (
        <>
          <div className="grid gap-3 sm:grid-cols-4">
            <Stat label="Designs evaluated" value={num(result.evaluated, 0)} />
            <Stat label="On the front" value={num(result.front.length, 0)} />
            <Stat
              label="Feasible"
              value={num(result.feasible_evaluated, 0)}
              tone={result.feasible_evaluated > 0 ? "good" : "bad"}
            />
            <Stat label="Search time" value={num(result.runtime_s, 0)} unit="s" />
          </div>

          <div className="h-[320px]">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 12, right: 16, bottom: 8, left: 4 }}>
                <CartesianGrid stroke={grid} strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  dataKey="degreeHours"
                  name="degree hours outside band"
                  unit=" Kh"
                  tick={axis}
                  stroke={grid}
                />
                <YAxis
                  type="number"
                  dataKey="cost"
                  name="envelope cost"
                  tick={axis}
                  stroke={grid}
                  width={70}
                  tickFormatter={(value: number) => `₹${Math.round(value / 1000)}k`}
                />
                <ZAxis type="number" dataKey="margin" range={[50, 320]} name="night margin" />
                <Tooltip
                  cursor={{ stroke: grid }}
                  contentStyle={{
                    background: "#0b1220",
                    border: "1px solid #22334c",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: unknown, name: string) => {
                    if (name === "envelope cost") return [rupees(value as number), name];
                    return [num(value as number, 1), name];
                  }}
                />
                <Scatter data={points} name="designs">
                  {points.map((point: (typeof points)[number]) => (
                    <Cell
                      key={point.hash}
                      fill={
                        point.knee
                          ? "var(--color-solar)"
                          : point.feasible
                            ? "var(--color-cold)"
                            : "var(--color-diesel)"
                      }
                      fillOpacity={point.feasible ? 0.85 : 0.5}
                    />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>

          <p className="text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
            Each dot is one simulated design. Left is more comfortable, down is cheaper,
            larger is a bigger margin on the worst night. Amber is the knee point, the
            design closest to the ideal corner once the three goals are normalised. Red
            dots broke a constraint and are shown rather than hidden.
            {result.warnings.length > 0 &&
              ` ${result.warnings.map((item: { message: string }) => item.message).join(" ")}`}
          </p>
        </>
      )}
    </div>
  );
}
