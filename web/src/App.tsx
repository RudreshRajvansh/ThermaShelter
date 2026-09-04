import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type DesignSpec } from "./lib/api";
import { STEPS, activeDesign, useStore, type Step } from "./lib/store";
import { Warnings } from "./components/Kit";
import { SiteInput } from "./pages/SiteInput";
import { ClimateData } from "./pages/ClimateData";
import { DesignParameters } from "./pages/DesignParameters";
import { Simulation } from "./pages/Simulation";
import { Results } from "./pages/Results";
import { Compare } from "./pages/Compare";
import { Reports } from "./pages/Reports";
import { num } from "./lib/format";

function Stepper({ step, onChange }: { step: Step; onChange: (step: Step) => void }) {
  const index = STEPS.indexOf(step);
  return (
    <nav className="flex flex-wrap items-center gap-1">
      {STEPS.map((item, position) => {
        const active = item === step;
        const done = position < index;
        return (
          <div key={item} className="flex items-center">
            <button
              type="button"
              onClick={() => onChange(item)}
              className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition ${
                active
                  ? "border-[var(--color-solar)] bg-[var(--color-solar)]/15 text-[var(--color-solar)]"
                  : done
                    ? "border-[var(--color-line)] text-[var(--color-ink-dim)]"
                    : "border-[var(--color-line-soft)] text-[var(--color-ink-faint)]"
              }`}
            >
              <span
                className={`flex h-4 w-4 items-center justify-center rounded-full text-[10px] ${
                  active
                    ? "bg-[var(--color-solar)] text-[#0b1220]"
                    : done
                      ? "bg-[var(--color-line)] text-[var(--color-ink)]"
                      : "border border-[var(--color-line)]"
                }`}
              >
                {position + 1}
              </span>
              {item}
            </button>
            {position < STEPS.length - 1 && (
              <span className="mx-0.5 h-px w-3 bg-[var(--color-line)]" />
            )}
          </div>
        );
      })}
    </nav>
  );
}

function ModeToggle() {
  const store = useStore();
  return (
    <div className="flex items-center gap-1 rounded-md border border-[var(--color-line)] p-0.5 text-[11px]">
      <button
        type="button"
        onClick={() => store.setLiveMode(false)}
        className={`rounded px-2 py-1 ${
          !store.liveMode
            ? "bg-[var(--color-good)]/20 text-[var(--color-good)]"
            : "text-[var(--color-ink-faint)]"
        }`}
        title="Stored weather file, zero network. This is the demo path."
      >
        ● Cached, offline
      </button>
      <button
        type="button"
        disabled
        className="cursor-not-allowed rounded px-2 py-1 text-[var(--color-ink-faint)] opacity-50"
        title="Live fetch of recent hourly weather is a Layer 0 capability and is not wired into this build. It is shown disabled rather than faked."
      >
        ○ Live
      </button>
    </div>
  );
}

export default function App() {
  const store = useStore();
  const { setSiteId, setCooling, setDesign } = store;

  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta, staleTime: Infinity });
  const references = useQuery({
    queryKey: ["references"],
    queryFn: api.references,
    staleTime: Infinity,
  });
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

  const siteId = store.siteId;
  useEffect(() => {
    if (siteId || !meta.data?.sites.length) return;
    setSiteId(meta.data.default_site_id ?? meta.data.sites[0].id);
  }, [meta.data, siteId, setSiteId]);

  const site = meta.data?.sites.find((item) => item.id === siteId);
  useEffect(() => {
    if (site) setCooling(site.climate_objective === "cooling_dominated");
  }, [site, setCooling]);

  const stored = useMemo(() => {
    const fromFiles = catalogue.data?.[store.designName];
    if (fromFiles) return fromFiles;
    return references.data?.find((item) => item.name === store.designName) ?? null;
  }, [catalogue.data, references.data, store.designName]);

  useEffect(() => {
    if (stored) setDesign(stored);
  }, [stored, setDesign]);

  const design = activeDesign(store);

  const geometry = useQuery({
    queryKey: ["geometry", design, siteId],
    queryFn: () => api.geometry(design!, siteId),
    enabled: Boolean(design && siteId),
    staleTime: Infinity,
  });

  const simulation = useQuery({
    queryKey: ["simulate", design, siteId, store.period.start, store.period.end],
    queryFn: () =>
      api.simulate({
        design: design!,
        site_id: siteId,
        period: store.period,
        surface_period: store.surfacePeriod,
        conditioned_run: true,
        surface_output: true,
        with_uncertainty: true,
        with_reference: true,
      }),
    enabled: Boolean(design && siteId),
    staleTime: Infinity,
  });

  const designOptions = [
    ...Object.keys(catalogue.data ?? {}),
    ...(references.data?.map((item) => item.name) ?? []),
  ];

  const blocking = (meta.data?.warnings ?? []).filter(
    (item) => item.severity === "severe" || item.severity === "fatal",
  );

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-[var(--color-line)] bg-[var(--color-night)]/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1360px] flex-wrap items-center gap-3 px-4 py-2.5">
          <div className="mr-2">
            <h1 className="display text-base leading-tight">Passive Shelter Design Tool</h1>
            <p className="text-[10px] uppercase tracking-wider text-[var(--color-ink-faint)]">
              DRDO · thermal comfort · Ladakh
            </p>
          </div>

          <div className="no-print ml-auto flex flex-wrap items-center gap-2">
            <select
              value={siteId ?? ""}
              onChange={(event) => setSiteId(event.target.value)}
              className="px-2 py-1 text-xs"
            >
              {meta.data?.sites.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <select
              value={store.designName}
              onChange={(event) => store.setDesignName(event.target.value)}
              className="px-2 py-1 text-xs"
            >
              {designOptions.map((name) => (
                <option key={name} value={name}>
                  {name.replace(/_/g, " ")}
                </option>
              ))}
            </select>
            <ModeToggle />
          </div>
        </div>
        <div className="no-print mx-auto max-w-[1360px] overflow-x-auto px-4 pb-2.5">
          <Stepper step={store.step} onChange={store.setStep} />
        </div>
      </header>

      <main className="mx-auto max-w-[1360px] space-y-4 px-4 py-4 pb-16">
        {blocking.length > 0 && <Warnings warnings={blocking} />}

        {site && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)]/60 px-3 py-2 text-[11px] text-[var(--color-ink-dim)]">
            <span>
              <span className="text-[var(--color-ink)]">{site.name}</span> ·{" "}
              {num(site.elevation_m, 0)} m · {site.climate_objective.replace(/_/g, " ")}
            </span>
            <span>
              leading with{" "}
              <span className="text-[var(--color-solar)]">
                {site.headline_metric.replace(/_/g, " ")}
              </span>
            </span>
            <span>
              simulated {store.period.start} → {store.period.end}
            </span>
            {store.overrides && (
              <span className="rounded bg-[var(--color-solar)]/15 px-1.5 py-0.5 text-[var(--color-solar)]">
                edited design
              </span>
            )}
            {meta.data && (
              <span className="ml-auto text-[var(--color-ink-faint)]">
                engine {meta.data.engine_version} · EnergyPlus{" "}
                {meta.data.energyplus_version ?? "not found"} · {meta.data.stored_runs}{" "}
                stored runs
              </span>
            )}
          </div>
        )}

        {store.step === "Site Input" && <SiteInput meta={meta.data} />}
        {store.step === "Climate Data" && <ClimateData />}
        {store.step === "Design Parameters" && <DesignParameters />}
        {store.step === "Simulation" && (
          <Simulation
            geometry={geometry.data}
            simulation={simulation.data}
            simulating={simulation.isPending}
            error={simulation.error ?? geometry.error}
          />
        )}
        {store.step === "Results" && (
          <Results
            simulation={simulation.data}
            simulating={simulation.isPending}
            error={simulation.error}
          />
        )}
        {store.step === "Compare Designs" && <Compare />}
        {store.step === "Reports" && (
          <Reports simulation={simulation.data} meta={meta.data} />
        )}
      </main>
    </div>
  );
}
