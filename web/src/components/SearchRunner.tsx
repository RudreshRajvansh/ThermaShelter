import { useEffect, useMemo, useRef } from "react";
import {
  api,
  type DesignSpec,
  type DesignVariable,
  type ParetoPoint,
  type ParetoResult,
} from "../lib/api";
import { activeDesign, describeDesign, useStore, type SearchState } from "../lib/store";
import { num } from "../lib/format";

export const LEVERS: DesignVariable[] = [
  { name: "wall.layers.2.thickness_m", kind: "real", low: 0.15, high: 0.55, options: null },
  { name: "wall.layers.1.thickness_m", kind: "real", low: 0.02, high: 0.18, options: null },
  { name: "windows.south.wwr", kind: "real", low: 0.08, high: 0.45, options: null },
];

export const LEVER_NAMES = "wall mass, insulation thickness and south glazing area";

export function bestPoint(result: ParetoResult | null): ParetoPoint | null {
  if (!result || result.front.length === 0) return null;
  const feasible = result.front.filter((point) => point.feasible);
  const pool = feasible.length > 0 ? feasible : result.front;
  return pool.find((point) => point.design_hash === result.knee_hash) ?? pool[0] ?? null;
}

export function SearchRunner() {
  const store = useStore();
  const { searchRequest, updateSearch, setPreview, search, siteId, period } = store;
  const design = activeDesign(store);
  const started = useRef<number | null>(null);
  const pushed = useRef<{ hash: string; label: string; design: DesignSpec } | null>(null);

  useEffect(() => {
    if (!searchRequest || started.current === searchRequest.token) return;
    if (!design || !siteId) return;
    started.current = searchRequest.token;
    pushed.current = null;
    updateSearch({ token: searchRequest.token, jobId: null, result: null, failure: null });
    const { budget } = searchRequest;
    api
      .optimise({
        base_design: design,
        variables: LEVERS,
        objectives: ["degree_hours_total_kh", "total_cost_inr", "worst_night_margin_k"],
        constraints: { max_condensation_hours: 2000, max_wall_thickness_m: 0.9 },
        population_size: budget.size,
        generations: Math.max(2, Math.round(budget.designs / budget.size)),
        seed: 1,
        site_id: siteId,
        period,
      })
      .then((job) => updateSearch({ jobId: job.job_id }))
      .catch((error: unknown) =>
        updateSearch({ failure: error instanceof Error ? error.message : String(error) }),
      );
  }, [searchRequest, design, siteId, period, updateSearch]);

  const jobId = search.jobId;
  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    const poll = async () => {
      try {
        const job = await api.optimisationStatus(jobId);
        if (stopped) return;
        const patch: Partial<SearchState> = {};
        if (job.result) patch.result = job.result as unknown as ParetoResult;
        if (job.status === "done") patch.jobId = null;
        if (job.status === "failed") {
          patch.failure = job.message ?? "the search failed";
          patch.jobId = null;
        }
        updateSearch(patch);
      } catch (error) {
        if (!stopped) updateSearch({ failure: String(error), jobId: null });
      }
    };
    void poll();
    const timer = window.setInterval(poll, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [jobId, updateSearch]);

  const result = search.result;
  const best = useMemo(() => bestPoint(result), [result]);
  useEffect(() => {
    if (!best || !result) return;
    const minimum = best.metrics?.minimum_overnight_operative_c;
    const reading = typeof minimum === "number" ? ` · ${num(minimum, 1)} °C overnight` : "";
    const label = result.complete
      ? `Recommended · ${describeDesign(best.design)}${reading} · ${result.evaluated} designs simulated`
      : `Best so far · ${describeDesign(best.design)}${reading} · generation ${result.generation} of ${result.generations_planned}`;
    const previous = pushed.current;
    if (previous && previous.hash === best.design_hash && previous.label === label) return;
    const shown = previous && previous.hash === best.design_hash ? previous.design : best.design;
    pushed.current = { hash: best.design_hash, label, design: shown };
    setPreview({ design: shown, label, hash: best.design_hash });
  }, [best, result, setPreview]);

  return null;
}
