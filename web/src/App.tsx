import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type DesignSpec } from "./lib/api";
import {
  DESIGN_TABS,
  EVIDENCE_TABS,
  activeDesign,
  useStore,
} from "./lib/store";
import { Tabs, Warnings } from "./components/Kit";
import { Navbar } from "./components/Navbar";
import { SiteInput } from "./pages/SiteInput";
import { ClimateData } from "./pages/ClimateData";
import { DesignParameters } from "./pages/DesignParameters";
import { Simulation } from "./pages/Simulation";
import { Results } from "./pages/Results";
import { Compare } from "./pages/Compare";
import { Recommend } from "./pages/Recommend";
import { Reports } from "./pages/Reports";
import { num } from "./lib/format";

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
      const listed = await api.designs();
      const entries = await Promise.all(
        listed
          .filter((item) => !item.is_reference)
          .map(async (item) => {
            try {
              return [item.id, await api.design(item.id)] as const;
            } catch {
              return null;
            }
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

  const site = useMemo(
    () => meta.data?.sites.find((item) => item.id === siteId),
    [meta.data, siteId],
  );
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
    queryKey: [
      "simulate",
      design,
      siteId,
      store.period.start,
      store.period.end,
      store.comfortModelId,
    ],
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
        comfort_model_id: store.comfortModelId,
      }),
    enabled: Boolean(design && siteId),
    staleTime: Infinity,
  });

  const candidateOptions = useMemo(
    () => Object.keys(catalogue.data ?? {}),
    [catalogue.data],
  );
  const referenceOptions = useMemo(
    () => references.data?.map((item) => item.name) ?? [],
    [references.data],
  );
  const designOptions = useMemo(
    () => [...candidateOptions, ...referenceOptions],
    [candidateOptions, referenceOptions],
  );

  const { designName, setDesignName } = store;
  useEffect(() => {
    if (designOptions.length === 0 || designOptions.includes(designName)) return;
    setDesignName(designOptions[0]);
  }, [designOptions, designName, setDesignName]);

  const blocking = (meta.data?.warnings ?? []).filter(
    (item) => item.severity === "severe" || item.severity === "fatal",
  );

  return (
    <div className="min-h-full">
      <Navbar
        meta={meta.data}
        siteId={siteId}
        candidateOptions={candidateOptions}
        referenceOptions={referenceOptions}
      />

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

        {store.step === "Design" && (
          <div className="space-y-4">
            <Tabs
              tabs={DESIGN_TABS}
              active={store.designTab}
              onChange={store.setDesignTab}
            />
            {store.designTab === "recommend" && (
              <div className="grid items-start gap-5 xl:grid-cols-[minmax(340px,400px)_1fr]">
                <Recommend meta={meta.data} />
                <div className="hidden xl:block">
                  <Simulation
                    viewportOnly
                    geometry={geometry.data}
                    simulation={simulation.data}
                    simulating={simulation.isPending}
                    error={simulation.error ?? geometry.error}
                  />
                </div>
              </div>
            )}
            {store.designTab === "model" && (
              <Simulation
                geometry={geometry.data}
                simulation={simulation.data}
                simulating={simulation.isPending}
                error={simulation.error ?? geometry.error}
              />
            )}
            {store.designTab === "all inputs" && <SiteInput meta={meta.data} />}
          </div>
        )}

        {store.step === "Evidence" && (
          <div className="space-y-4">
            <Tabs
              tabs={EVIDENCE_TABS}
              active={store.evidenceTab}
              onChange={store.setEvidenceTab}
            />
            {store.evidenceTab === "climate" && <ClimateData />}
            {store.evidenceTab === "construction" && <DesignParameters />}
            {store.evidenceTab === "results" && (
              <Results
                simulation={simulation.data}
                simulating={simulation.isPending}
                error={simulation.error}
              />
            )}
            {store.evidenceTab === "compare" && <Compare />}
          </div>
        )}

        {store.step === "Report" && (
          <Reports simulation={simulation.data} meta={meta.data} />
        )}
      </main>
    </div>
  );
}
