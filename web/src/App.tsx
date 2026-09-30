import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type DesignSpec } from "./lib/api";
import { activeDesign, useStore } from "./lib/store";
import { Warnings } from "./components/Kit";
import { Navbar } from "./components/Navbar";
import { SearchRunner } from "./components/SearchRunner";
import { InputPage } from "./pages/InputPage";
import { OutputPage } from "./pages/OutputPage";
import { OperatePage } from "./pages/OperatePage";

export default function App() {
  const store = useStore();
  const { setSiteId, setCooling, setDesign } = store;

  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta, staleTime: Infinity });
  const references = useQuery({
    queryKey: ["references"],
    queryFn: api.references,
    staleTime: Infinity,
  });
  const summaries = useQuery({
    queryKey: ["design-summaries"],
    queryFn: api.designs,
    staleTime: Infinity,
  });
  const catalogue = useQuery({
    queryKey: ["design-catalogue", summaries.data],
    queryFn: async (): Promise<Record<string, DesignSpec>> => {
      const entries = await Promise.all(
        (summaries.data ?? [])
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
    enabled: Boolean(summaries.data),
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
  const onResults = store.page === "output";

  const geometry = useQuery({
    queryKey: ["geometry", design, siteId],
    queryFn: () => api.geometry(design!, siteId),
    enabled: onResults && Boolean(design && siteId),
    staleTime: Infinity,
  });

  const previewGeometry = useQuery({
    queryKey: ["geometry", store.preview?.design, siteId],
    queryFn: () => api.geometry(store.preview!.design, siteId),
    enabled: onResults && Boolean(store.preview && siteId),
    staleTime: Infinity,
    placeholderData: (previous) => previous,
  });

  const simulation = useQuery({
    queryKey: ["simulate", design, siteId, store.period.start, store.period.end, store.comfortModelId],
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
    enabled: onResults && Boolean(design && siteId),
    staleTime: Infinity,
  });

  const designOptions = useMemo(
    () => [
      ...Object.keys(catalogue.data ?? {}),
      ...(references.data?.map((item) => item.name) ?? []),
    ],
    [catalogue.data, references.data],
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
      <Navbar meta={meta.data} ready={Boolean(site && design)} />
      <SearchRunner />
      <main className="mx-auto max-w-[1360px] space-y-4 px-4 py-6 pb-16">
        {blocking.length > 0 && <Warnings warnings={blocking} />}
        {store.page === "operate" ? (
          <OperatePage meta={meta.data} />
        ) : store.page === "input" ? (
          <InputPage meta={meta.data} catalogue={catalogue.data} summaries={summaries.data} />
        ) : (
          <OutputPage
            meta={meta.data}
            geometry={geometry.data}
            previewGeometry={previewGeometry.data}
            simulation={simulation.data}
            simulating={simulation.isPending}
            error={simulation.error ?? geometry.error}
          />
        )}
      </main>
    </div>
  );
}
