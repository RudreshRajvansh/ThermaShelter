import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
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
import {
  api,
  type DesignSpec,
  type DesignVariable,
  type MetaResponse,
  type ParetoPoint,
  type ParetoResult,
  type ShelterUseSummary,
} from "../lib/api";
import { Failure, Field, Warnings } from "../components/Kit";
import { activeDesign, useStore } from "../lib/store";
import { num, rupees } from "../lib/format";

const axis = { fontSize: 11, fill: "var(--color-ink-faint)" };

const LEVERS: DesignVariable[] = [
  { name: "wall.layers.2.thickness_m", kind: "real", low: 0.15, high: 0.55, options: null },
  { name: "wall.layers.1.thickness_m", kind: "real", low: 0.02, high: 0.18, options: null },
  { name: "windows.south.wwr", kind: "real", low: 0.08, high: 0.45, options: null },
];

const LEVER_NAMES = "wall mass, insulation thickness and south glazing area";

const BUDGETS = [
  { designs: 100, size: 20, label: "Quick look", detail: "about four minutes" },
  { designs: 300, size: 25, label: "Standard", detail: "about twelve minutes" },
  { designs: 500, size: 25, label: "Thorough", detail: "about twenty minutes" },
];

function set<T extends object>(base: T, patch: Partial<T>): T {
  return { ...base, ...patch };
}

function describe(design: DesignSpec): string {
  const words = design.arrangement.replace(/_/g, " ");
  const arrangement = words.charAt(0).toUpperCase() + words.slice(1);
  const wall = design.wall.layers.reduce(
    (total: number, layer: { thickness_m: number }) => total + layer.thickness_m,
    0,
  );
  return `${arrangement}, ${Math.round(wall * 1000)} mm wall`;
}

export function Recommend({ meta }: { meta: MetaResponse | undefined }) {
  const store = useStore();
  const design = activeDesign(store);

  const [budget, setBudget] = useState(BUDGETS[0]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<ParetoResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const uses = useQuery({
    queryKey: ["shelter-uses"],
    queryFn: api.shelterUses,
    staleTime: Infinity,
  });
  const library = useQuery({
    queryKey: ["design-summaries"],
    queryFn: api.designs,
    staleTime: Infinity,
  });

  const { designName, setDesignName } = store;
  const candidates = useMemo(
    () => (library.data ?? []).filter((item) => !item.is_reference),
    [library.data],
  );
  useEffect(() => {
    if (candidates.length === 0) return;
    if (candidates.some((item) => item.id === designName)) return;
    const preferred =
      candidates.find((item) => item.arrangement === "sunspace") ?? candidates[0];
    setDesignName(preferred.id);
  }, [candidates, designName, setDesignName]);

  const programme: ShelterUseSummary | undefined = (uses.data ?? []).find(
    (item) => item.use === design?.plan.use,
  );

  const update = (patch: Partial<DesignSpec>) => {
    if (design) store.applyOverride(set(design, patch));
  };

  const applyUse = (value: string) => {
    const chosen = (uses.data ?? []).find((item) => item.use === value);
    if (!chosen || !design) return;
    const match = candidates.find((item) => item.use === value);
    if (match && match.id !== store.designName) {
      store.setDesignName(match.id);
      store.clearOverride();
      return;
    }
    update({
      plan: set(design.plan, {
        use: chosen.plan.use,
        floor_area_m2: chosen.plan.floor_area_m2,
        aspect_ratio: chosen.plan.aspect_ratio,
        ceiling_height_m: chosen.plan.ceiling_height_m,
      }),
      occupancy: chosen.occupancy,
    });
  };

  const start = useMutation({
    mutationFn: async () => {
      if (!design) throw new Error("no design loaded");
      setResult(null);
      setFailure(null);
      return api.optimise({
        base_design: design,
        variables: LEVERS,
        objectives: ["degree_hours_total_kh", "total_cost_inr", "worst_night_margin_k"],
        constraints: { max_condensation_hours: 2000, max_wall_thickness_m: 0.9 },
        population_size: budget.size,
        generations: Math.max(2, Math.round(budget.designs / budget.size)),
        seed: 1,
        site_id: store.siteId,
        period: { start: store.period.start, end: store.period.start },
      });
    },
    onSuccess: (job) => setJobId(job.job_id),
  });

  useEffect(() => {
    if (!jobId) return;
    const poll = async () => {
      try {
        const job = await api.optimisationStatus(jobId);
        if (job.result) setResult(job.result as unknown as ParetoResult);
        if (job.status === "done") setJobId(null);
        if (job.status === "failed") {
          setFailure(job.message ?? "the search failed");
          setJobId(null);
        }
      } catch (error) {
        setFailure(String(error));
        setJobId(null);
      }
    };
    void poll();
    timer.current = window.setInterval(poll, 1500);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [jobId]);

  const best: ParetoPoint | null = useMemo(() => {
    if (!result || result.front.length === 0) return null;
    const feasible = result.front.filter((point) => point.feasible);
    const pool = feasible.length > 0 ? feasible : result.front;
    return (
      pool.find((point) => point.design_hash === result.knee_hash) ?? pool[0] ?? null
    );
  }, [result]);

  const points = (result?.front ?? []).map((point) => ({
    degreeHours: point.objectives.degree_hours_total_kh ?? 0,
    cost: point.objectives.total_cost_inr ?? 0,
    feasible: point.feasible,
    knee: point.design_hash === result?.knee_hash,
    name: describe(point.design),
  }));

  const searching = Boolean(jobId);
  const site = meta?.sites.find((item) => item.id === store.siteId);
  const headline = site?.headline_metric ?? "minimum_overnight_operative_c";

  if (!design || !meta) {
    return <p className="text-sm text-[var(--color-ink-dim)]">Loading the design library…</p>;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <section className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
            Design a shelter
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-[var(--color-ink-dim)]">
            Answer three questions. Every candidate the search considers is a full
            EnergyPlus run against the stored weather year for this location, so the
            answer takes minutes rather than seconds and nothing on this page is
            estimated.
          </p>
        </div>

        <div className="grid gap-5 sm:grid-cols-3">
          <Field label="Location">
            <select
              value={store.siteId ?? ""}
              onChange={(event) => store.setSiteId(event.target.value)}
              disabled={searching}
              className="w-full px-3 py-2 text-sm"
            >
              {meta.sites.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="What it is for">
            <select
              value={design.plan.use}
              onChange={(event) => applyUse(event.target.value)}
              disabled={searching || !uses.data}
              className="w-full px-3 py-2 text-sm"
            >
              {(uses.data ?? []).map((item) => (
                <option key={item.use} value={item.use}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Occupants">
            <input
              type="number"
              min={0}
              max={60}
              value={design.occupancy.occupants}
              disabled={searching}
              onChange={(event) =>
                update({
                  occupancy: set(design.occupancy, {
                    occupants: Number(event.target.value),
                  }),
                })
              }
              className="w-full px-3 py-2 text-sm"
            />
          </Field>
        </div>

        {programme && (
          <p className="text-[13px] leading-relaxed text-[var(--color-ink-faint)]">
            {programme.description} The programme sets{" "}
            {num(design.plan.floor_area_m2, 0)} m² over{" "}
            {programme.room_names.length} rooms at{" "}
            {programme.ceiling_height_m.toFixed(2)} m. Change any of it under advanced
            options.
          </p>
        )}

        <details className="group border-t border-[var(--color-line)] pt-4">
          <summary className="cursor-pointer list-none text-sm font-medium text-[var(--color-ink-dim)] transition-colors hover:text-[var(--color-ink)]">
            <span className="inline-block w-4 transition-transform group-open:rotate-90">
              ›
            </span>
            Advanced options
          </summary>

          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <Field label="Floor area" hint="square metres">
              <input
                type="number"
                min={4}
                max={400}
                value={design.plan.floor_area_m2}
                disabled={searching}
                onChange={(event) =>
                  update({
                    plan: set(design.plan, {
                      floor_area_m2: Number(event.target.value),
                    }),
                  })
                }
                className="w-full px-3 py-2 text-sm"
              />
            </Field>

            <Field
              label="Orientation"
              hint="degrees the long axis is turned from due south"
            >
              <input
                type="number"
                min={-180}
                max={180}
                value={design.plan.azimuth_deg}
                disabled={searching}
                onChange={(event) =>
                  update({
                    plan: set(design.plan, {
                      azimuth_deg: Number(event.target.value),
                    }),
                  })
                }
                className="w-full px-3 py-2 text-sm"
              />
            </Field>

            <Field
              label="Comfort target"
              hint="the adaptive band the result is judged against"
            >
              <select
                value={store.comfortModelId ?? meta.comfort_model_id}
                disabled={searching}
                onChange={(event) => store.setComfortModelId(event.target.value)}
                className="w-full px-3 py-2 text-sm"
              >
                {meta.comfort_models.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </Field>

            <Field
              label="Search budget"
              hint="every design is one EnergyPlus run, so this is real time"
            >
              <select
                value={budget.designs}
                disabled={searching}
                onChange={(event) =>
                  setBudget(
                    BUDGETS.find(
                      (item) => item.designs === Number(event.target.value),
                    ) ?? BUDGETS[0],
                  )
                }
                className="w-full px-3 py-2 text-sm"
              >
                {BUDGETS.map((item) => (
                  <option key={item.designs} value={item.designs}>
                    {item.label} — {item.designs} designs, {item.detail}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <p className="mt-5 text-[13px] leading-relaxed text-[var(--color-ink-faint)]">
            The search is free to move {LEVER_NAMES}. Everything else stays as you set
            it here. To edit constructions layer by layer, or to compare designs you
            have chosen yourself, use the full analysis.
          </p>
        </details>

        <div className="flex flex-wrap items-center gap-4 border-t border-[var(--color-line)] pt-5">
          <button
            type="button"
            onClick={() => start.mutate()}
            disabled={searching || start.isPending}
            className="rounded-lg bg-[var(--color-solar)] px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-all hover:shadow-md focus:outline-none focus:ring-2 focus:ring-[var(--color-solar)] focus:ring-offset-2 disabled:opacity-40 disabled:shadow-none"
          >
            {searching ? "Searching…" : "Find the best shelter"}
          </button>
          <button
            type="button"
            onClick={() => store.setStep("Site Input")}
            className="text-sm text-[var(--color-ink-faint)] underline underline-offset-4 transition-colors hover:text-[var(--color-ink)]"
          >
            Full analysis
          </button>
        </div>
      </section>

      {failure && <Failure error={failure} />}
      {start.isError && <Failure error={start.error} />}

      {(searching || result) && (
        <section className="space-y-6 border-t border-[var(--color-line)] pt-8">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-lg font-semibold tracking-tight text-[var(--color-ink)]">
              {searching ? "Searching" : "Recommended"}
            </h2>
            {result && (
              <p className="text-[13px] tabular-nums text-[var(--color-ink-faint)]">
                {result.evaluated} designs simulated
                {!result.complete &&
                  ` · generation ${result.generation} of ${result.generations_planned}`}
                {result.complete && ` · ${num(result.runtime_s, 0)} s`}
              </p>
            )}
          </div>

          {searching && !result && (
            <div className="space-y-3">
              <div className="h-1 w-full overflow-hidden rounded-full bg-[var(--color-line-soft)]">
                <div className="h-full w-1/3 animate-pulse rounded-full bg-[var(--color-solar)]" />
              </div>
              <p className="text-sm text-[var(--color-ink-dim)]">
                The first generation is running. Results appear here as soon as the
                first designs finish, and improve while the search continues.
              </p>
            </div>
          )}

          {best && (
            <div className="space-y-5">
              <div>
                <p className="text-xl font-semibold tracking-tight text-[var(--color-ink)]">
                  {describe(best.design)}
                </p>
                <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-ink-dim)]">
                  Of {result?.evaluated} shelters simulated, this one sits closest to
                  the ideal corner once warmth, cost and the worst design night are
                  weighed together. It is a recommendation, not a verdict: the designs
                  plotted below are all defensible, and choosing between them is a
                  judgement about budget and buildability.
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
                    <dt className="text-[13px] text-[var(--color-ink-faint)]">
                      {item.term}
                    </dt>
                    <dd className="mt-1 text-lg font-semibold tabular-nums text-[var(--color-ink)]">
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
                <ul className="space-y-1.5 text-[13px] text-[var(--color-ink-dim)]">
                  {best.flags.map((flag) => (
                    <li key={flag}>{flag.replace(/_/g, " ")}</li>
                  ))}
                </ul>
              )}

              <div className="flex flex-wrap gap-4 pt-1">
                <button
                  type="button"
                  onClick={() => {
                    store.applyOverride(best.design);
                    store.setStep("Simulation");
                  }}
                  className="text-sm text-[var(--color-solar)] underline underline-offset-4"
                >
                  See it in 3D
                </button>
                <button
                  type="button"
                  onClick={() => {
                    store.applyOverride(best.design);
                    store.setStep("Results");
                  }}
                  className="text-sm text-[var(--color-solar)] underline underline-offset-4"
                >
                  Full result for this design
                </button>
              </div>
            </div>
          )}

          {points.length > 0 && (
            <div className="space-y-3 border-t border-[var(--color-line)] pt-6">
              <p className="text-sm text-[var(--color-ink-dim)]">
                Every design the search simulated. Left is warmer, down is cheaper. The
                highlighted one is the recommendation.
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
                    <YAxis
                      type="number"
                      dataKey="cost"
                      name="cost"
                      tick={axis}
                      stroke="var(--color-line)"
                      width={72}
                    />
                    <Tooltip
                      cursor={{ strokeDasharray: "3 3" }}
                      contentStyle={{
                        background: "var(--color-day)",
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
                Red points broke a constraint and are shown rather than hidden. Rupee
                figures come from delivered material rates that carry no verified
                source yet, so treat the cost axis as relative rather than absolute.
              </p>
            </div>
          )}

          {result && result.warnings.length > 0 && (
            <Warnings warnings={result.warnings} />
          )}
        </section>
      )}
    </div>
  );
}
