import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type DesignSpec } from "./lib/api";
import { activeDesign, useStore } from "./lib/store";

import { Sidebar } from "./components/Sidebar";
import { Header } from "./components/Header";
import { Warnings } from "./components/Kit";
import { SiteInput } from "./pages/SiteInput";
import { ClimateData } from "./pages/ClimateData";
import { DesignParameters } from "./pages/DesignParameters";
import { Simulation } from "./pages/Simulation";
import { Results } from "./pages/Results";
import { Compare } from "./pages/Compare";
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

  const previewGeometry = useQuery({
    queryKey: ["geometry", store.preview?.design, siteId],
    queryFn: () => api.geometry(store.preview!.design, siteId),
    enabled: Boolean(store.preview && siteId),
    staleTime: Infinity,
    placeholderData: (previous) => previous,
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

  const siteInfoCard = site ? (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-xl border border-[var(--color-solar)]/20 bg-indigo-50/50 px-4 py-3 text-[11px] font-medium text-[var(--color-ink-dim)] shadow-sm">
      <span>
        <span className="text-[var(--color-ink)] font-bold">{site.name}</span> ·{" "}
        {num(site.elevation_m, 0)} m · {site.climate_objective.replace(/_/g, " ")}
      </span>
      <span>
        leading with{" "}
        <span className="text-[var(--color-solar)] font-bold drop-shadow-sm">
          {site.headline_metric.replace(/_/g, " ")}
        </span>
      </span>
      <span>
        simulated {store.period.start} → {store.period.end}
      </span>
      {store.overrides && (
        <span className="rounded-md bg-[var(--color-solar)] px-2 py-0.5 text-white font-semibold shadow-sm">
          edited design
        </span>
      )}
    </div>
  ) : null;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[var(--color-night-2)] font-sans text-[var(--color-ink)]">
      <Sidebar />
      
      <div className="flex-1 flex flex-col h-full overflow-hidden relative z-0">
        <Header
          meta={meta.data}
          siteId={siteId}
          candidateOptions={candidateOptions}
          referenceOptions={referenceOptions}
        />

        <main className="flex-1 overflow-hidden">
          {store.step === "Site Input" && (
            <div className="flex h-full w-full">
              {/* Left Pane: Configuration */}
              <div className="w-[420px] lg:w-[480px] flex-shrink-0 h-full overflow-y-auto border-r border-[var(--color-line)] bg-[var(--color-night-2)] shadow-[4px_0_24px_rgba(0,0,0,0.02)] z-10 custom-scrollbar relative">
                <div className="p-6 space-y-6">
                  {blocking.length > 0 && <Warnings warnings={blocking} />}
                  {siteInfoCard}
                  <SiteInput meta={meta.data} />
                </div>
              </div>

              {/* Right Pane: 3D Simulation Canvas */}
              <div className="flex-1 h-full relative bg-white overflow-hidden">
                <div className="h-full w-full">
                  <Simulation
                    viewportOnly={true}
                    preview={store.preview}
                    geometry={store.preview ? previewGeometry.data : geometry.data}
                    simulation={simulation.data}
                    simulating={simulation.isPending}
                    error={simulation.error ?? geometry.error}
                  />
                </div>
              </div>
            </div>
          )}

          {store.step === "Simulation" && (
             <div className="h-full w-full overflow-y-auto bg-[var(--color-night-3)] custom-scrollbar">
                <div className="max-w-[1000px] mx-auto p-6 space-y-6">
                   {blocking.length > 0 && <Warnings warnings={blocking} />}
                   {siteInfoCard}
                   <Simulation
                     viewportOnly={false}
                     preview={store.preview}
                     geometry={store.preview ? previewGeometry.data : geometry.data}
                     simulation={simulation.data}
                     simulating={simulation.isPending}
                     error={simulation.error ?? geometry.error}
                   />
                </div>
             </div>
          )}

          {["Climate Data", "Design Parameters", "Results", "Compare Designs", "Reports"].includes(store.step) && (
            <div className="h-full w-full overflow-y-auto bg-[var(--color-night-2)] custom-scrollbar">
              <div className="max-w-[1200px] mx-auto p-6 sm:p-8 space-y-6">
                {blocking.length > 0 && <Warnings warnings={blocking} />}
                {siteInfoCard}
                
                <div className="mt-2">
                  {store.step === "Climate Data" && <ClimateData />}
                  {store.step === "Design Parameters" && <DesignParameters />}
                  {store.step === "Results" && (
                    <Results
                      simulation={simulation.data}
                      simulating={simulation.isPending}
                      error={simulation.error}
                    />
                  )}
                  {store.step === "Compare Designs" && <Compare />}
                  {store.step === "Reports" && <Reports simulation={simulation.data} meta={meta.data} />}
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
