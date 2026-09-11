import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GeometryPayload, type SimulationResult } from "../lib/api";
import { Failure, Loading, Panel, Stat, Tabs, Warnings } from "../components/Kit";
import { Scene3D } from "../components/Scene3D";
import { PlanView, SectionView } from "../components/PlanView";
import { ConstructionView } from "../components/Construction";
import { activeDesign, useStore } from "../lib/store";
import { num } from "../lib/format";

const VIEWS = ["3d view", "plan view", "sections", "construction"] as const;
type View = (typeof VIEWS)[number];

export function Simulation({
  geometry,
  simulation,
  simulating,
  error,
}: {
  geometry: GeometryPayload | undefined;
  simulation: SimulationResult | undefined;
  simulating: boolean;
  error: unknown;
}) {
  const store = useStore();
  const design = activeDesign(store);
  const [view, setView] = useState<View>("3d view");
  const [sectionAxis, setSectionAxis] = useState<"north-south" | "east-west">(
    "north-south",
  );

  const materials = useQuery({
    queryKey: ["materials"],
    queryFn: api.materials,
    staleTime: Infinity,
  });
  const sunPath = useQuery({
    queryKey: ["sun-path", store.siteId, store.surfacePeriod.start],
    queryFn: () => api.sunPath(store.siteId, store.surfacePeriod.start),
    enabled: Boolean(store.siteId),
    staleTime: Infinity,
  });
  const terrain = useQuery({
    queryKey: ["terrain", store.siteId],
    queryFn: () => api.terrain(store.siteId),
    enabled: Boolean(store.siteId),
    staleTime: Infinity,
  });

  const surfaces = simulation?.surfaces ?? null;
  const hours = surfaces?.timestamps ?? [];
  const index = Math.min(store.hourIndex, Math.max(0, hours.length - 1));

  return (
    <div className="space-y-4">
      <Panel title="Simulation Status">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Run period"
            value={`${store.period.start} → ${store.period.end}`}
            note="hourly, six timesteps per hour"
          />
          <Stat
            label="Per surface output"
            value={`${store.surfacePeriod.start} → ${store.surfacePeriod.end}`}
          />
          <Stat
            label="Solver"
            value={simulation ? `EnergyPlus ${simulation.energyplus_version}` : "EnergyPlus"}
            note={simulation ? `${num(simulation.runtime_s, 1)} s` : "waiting"}
          />
          <Stat
            label="Status"
            value={simulating ? "running" : simulation ? "complete" : "idle"}
            tone={simulating ? "solar" : simulation ? "good" : "neutral"}
            note={simulation?.cached ? "served from cache" : undefined}
          />
        </div>
        {error ? <div className="mt-3"><Failure error={error} /></div> : null}
      </Panel>

      <Panel
        title="Parametric Geometry"
        right={
          <div className="flex gap-1.5 p-1 bg-slate-100/80 rounded-xl shadow-inner">
            {(["construction", "surface_temperature", "incident_solar"] as const).map(
              (mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => store.setDisplayMode(mode)}
                  disabled={mode !== "construction" && !surfaces}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-300 ${
                    store.displayMode === mode
                      ? "bg-white text-[var(--color-solar)] shadow-sm"
                      : "text-slate-500 hover:text-slate-700 hover:bg-slate-200/50 disabled:opacity-40"
                  }`}
                >
                  {mode === "construction"
                    ? "Construction"
                    : mode === "surface_temperature"
                      ? "Surface temp."
                      : "Incident solar"}
                </button>
              ),
            )}
          </div>
        }
      >
        <Tabs tabs={VIEWS} active={view} onChange={setView} />

        <div className="mt-4">
          {!geometry ? (
            <Loading label="building geometry…" />
          ) : view === "3d view" ? (
            <Scene3D
              geometry={geometry}
              surfaces={surfaces}
              sunPath={sunPath.data}
              terrain={terrain.data}
              airflow={simulation?.airflow}
              mode={store.displayMode}
              onModeChange={store.setDisplayMode}
              hourIndex={index}
              onHourChange={store.setHourIndex}
            />
          ) : view === "plan view" ? (
             <PlanView
              geometry={geometry}
              surfaces={surfaces}
              hourIndex={index}
              mode={store.displayMode}
            />
          ) : view === "sections" ? (
            <div className="space-y-4">
              <div className="flex gap-2">
                {(["north-south", "east-west"] as const).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setSectionAxis(item)}
                    className={`rounded-lg border px-4 py-1.5 text-xs font-medium transition-all focus-visible:ring-2 focus-visible:ring-[var(--color-solar)] focus:outline-none ${
                      sectionAxis === item
                        ? "border-transparent bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
                        : "border-slate-200 text-slate-500 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700"
                    }`}
                  >
                    Section {item === "north-south" ? "A–A" : "B–B"} ({item})
                  </button>
                ))}
              </div>
              <SectionView
                geometry={geometry}
                surfaces={surfaces}
                hourIndex={index}
                mode={store.displayMode}
                axis={sectionAxis}
              />
            </div>
          ) : design ? (
            <ConstructionView design={design} materials={materials.data} />
          ) : null}
        </div>

        {view !== "3d view" && hours.length > 0 && (
          <div className="mt-5 flex items-center gap-4 bg-slate-50 p-3 rounded-xl border border-slate-100">
            <input
              type="range"
              aria-label="Hour index"
              min={0}
              max={hours.length - 1}
              value={index}
              onChange={(event) => store.setHourIndex(Number(event.target.value))}
              className="flex-1 cursor-pointer"
            />
            <span className="tnum w-40 text-right text-xs font-medium text-slate-600 bg-white px-3 py-1.5 rounded-lg shadow-sm border border-slate-200">
              {hours[index]?.replace("T", " ")}
            </span>
          </div>
        )}
      </Panel>

      {simulation && simulation.warnings.length > 0 && (
        <Panel title="Solver Warnings">
          <Warnings warnings={simulation.warnings} />
        </Panel>
      )}

      <div className="flex justify-end pt-2">
        <button
          type="button"
          onClick={() => store.setStep("Results")}
          disabled={!simulation}
          className="rounded-full bg-gradient-to-r from-[var(--color-solar)] to-indigo-500 px-8 py-3 text-[15px] font-semibold text-white transition-all hover:brightness-110 active:scale-95 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-[var(--color-solar)] focus:outline-none shadow-md hover:shadow-lg shadow-indigo-500/25 disabled:opacity-40 disabled:pointer-events-none disabled:shadow-none flex items-center gap-2"
        >
          See the results <span className="text-lg leading-none">→</span>
        </button>
      </div>
    </div>
  );
}
