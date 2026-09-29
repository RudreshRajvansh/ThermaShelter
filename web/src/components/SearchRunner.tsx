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

export const LEVER_NAMES =
  "wall mass and its material, wall, roof and floor insulation and their material, " +
  "plan proportion, ceiling height, orientation, south glazing, shutters, glazing type, " +
  "arrangement and a phase change layer";

export const OBJECTIVES = [
  "night_degree_hours_below_kh",
  "heating_litres",
  "imported_tonnes",
] as const;

export const OBJECTIVE_LABEL: Record<string, { label: string; unit: string }> = {
  night_degree_hours_below_kh: { label: "night degree hours below 14 °C", unit: "Kh" },
  heating_litres: { label: "kerosene to hold 14 °C at night", unit: "L" },
  imported_tonnes: { label: "material brought in from outside Ladakh", unit: "t" },
};

async function leversFor(design: DesignSpec, siteId: string): Promise<DesignVariable[]> {
  const levers = await api.searchLevers(design, siteId);
  return levers.map((lever) => lever.variable);
}

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
    leversFor(design, siteId)
      .then((variables) =>
        api.optimise({
          base_design: design,
          variables,
          objectives: [...OBJECTIVES],
          constraints: { max_condensation_hours: 2000, max_wall_thickness_m: 0.9 },
          population_size: budget.size,
          generations: Math.max(2, Math.round(budget.designs / budget.size)),
          seed: 1,
          robust_top: 5,
          robust_keep: 3,
          site_id: siteId,
          period,
        }),
      )
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
