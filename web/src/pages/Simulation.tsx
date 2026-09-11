import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GeometryPayload, type SimulationResult } from "../lib/api";
import { Failure, Loading, Panel, Stat, Tabs, Warnings } from "../components/Kit";
import { Scene3D } from "../components/Scene3D";
import { PlanView, SectionView } from "../components/PlanView";
import { ConstructionView } from "../components/Construction";
import { activeDesign, useStore, type Preview } from "../lib/store";
import { num } from "../lib/format";

const VIEWS = ["3d view", "plan view", "sections", "construction"] as const;
type View = (typeof VIEWS)[number];

export function Simulation({
  geometry,
  simulation,
  simulating,
  error,
  viewportOnly = false,
  preview = null,
}: {
  viewportOnly?: boolean;
  preview?: Preview | null;
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
      {!viewportOnly && (
      <Panel
        title="Simulation setup"
        subtitle="EnergyPlus runs a free floating case for comfort and a conditioned case for remaining heating demand, then eight corner runs for the uncertainty band and one run of the reference design."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Run period"
            value={`${store.period.start} → ${store.period.end}`}
            note="hourly, six timesteps per hour"
          />
          <Stat
            label="Per surface output"
            value={`${store.surfacePeriod.start} → ${store.surfacePeriod.end}`}
            note="a representative window, not the whole period"
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
      )}

      {viewportOnly ? (
        !geometry ? (
          <div className="flex h-[70vh] items-center justify-center rounded-xl border border-[var(--color-line)] bg-[var(--color-night-2)]">
            <Loading label="building geometry…" />
          </div>
        ) : (
          <Scene3D
            immersiveChrome
            geometry={geometry}
            surfaces={preview ? null : surfaces}
            sunPath={sunPath.data}
            terrain={terrain.data}
            airflow={preview ? null : simulation?.airflow}
            designOverride={preview?.design ?? null}
            badge={preview?.label ?? null}
            mode={preview ? "construction" : store.displayMode}
            onModeChange={store.setDisplayMode}
            hourIndex={index}
            onHourChange={store.setHourIndex}
          />
        )
      ) : (
      <Panel
        title="The geometry that was simulated"
        subtitle="Parametric, not a render. It rebuilds from your inputs, and the face names are the simulator's surface identifiers, so a painted value sits on the surface it belongs to."
        right={
          <div className="flex gap-1">
            {(["construction", "surface_temperature", "incident_solar"] as const).map(
              (mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => store.setDisplayMode(mode)}
                  disabled={mode !== "construction" && !surfaces}
                  className={`rounded-md border px-2.5 py-1 text-[11px] transition ${
                    store.displayMode === mode
                      ? "border-[var(--color-solar)] bg-[var(--color-solar)] text-white"
                      : "border-[var(--color-line)] text-[var(--color-ink-dim)] disabled:opacity-40"
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

        <div className="mt-3">
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
            <div className="space-y-2">
              <div className="flex gap-1">
                {(["north-south", "east-west"] as const).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setSectionAxis(item)}
                    className={`rounded-md border px-2.5 py-1 text-[11px] ${
                      sectionAxis === item
                        ? "border-[var(--color-solar)] text-[var(--color-solar-ink)]"
                        : "border-[var(--color-line)] text-[var(--color-ink-dim)]"
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
          <div className="mt-3 flex items-center gap-3">
            <input
              type="range"
              min={0}
              max={hours.length - 1}
              value={index}
              onChange={(event) => store.setHourIndex(Number(event.target.value))}
              className="flex-1"
            />
            <span className="tnum w-40 text-right text-xs text-[var(--color-ink-dim)]">
              {hours[index]?.replace("T", " ")}
            </span>
          </div>
        )}
      </Panel>
      )}

      {!viewportOnly && simulation && simulation.warnings.length > 0 && (
        <Panel
          title="What the simulation reported about itself"
          subtitle="Passed straight up from the EnergyPlus error file. A tool that reports its own problems is more credible than one that hides them."
        >
          <Warnings warnings={simulation.warnings} />
        </Panel>
      )}

      {!viewportOnly && (
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => store.open("performance")}
          disabled={!simulation}
          className="rounded-md bg-[var(--color-solar)] px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          See the results →
        </button>
      </div>
      )}
    </div>
  );
}
