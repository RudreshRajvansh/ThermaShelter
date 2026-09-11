import type { GeometryPayload, MetaResponse, SimulationResult } from "../lib/api";
import { Tabs } from "../components/Kit";
import { SearchPanel } from "../components/SearchPanel";
import { formatCoordinate } from "../components/LocationPicker";
import { OUTPUT_TABS, activeDesign, describeDesign, useStore } from "../lib/store";
import { num } from "../lib/format";
import { Simulation } from "./Simulation";
import { ClimateData } from "./ClimateData";
import { DesignParameters } from "./DesignParameters";
import { Results } from "./Results";
import { Compare } from "./Compare";
import { Reports } from "./Reports";

export function OutputPage({
  meta,
  geometry,
  previewGeometry,
  simulation,
  simulating,
  error,
}: {
  meta: MetaResponse | undefined;
  geometry: GeometryPayload | undefined;
  previewGeometry: GeometryPayload | undefined;
  simulation: SimulationResult | undefined;
  simulating: boolean;
  error: unknown;
}) {
  const store = useStore();
  const design = activeDesign(store);
  const site = meta?.sites.find((item) => item.id === store.siteId);
  const tab = store.outputTab;

  return (
    <div className="space-y-4">
      <div className="panel flex flex-wrap items-center justify-between gap-4 p-4 sm:p-5">
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-ink-faint)]">
            Results for
          </p>
          <h1 className="truncate text-xl font-semibold tracking-tight text-[var(--color-ink)]">
            {site?.name ?? "No site selected"}
          </h1>
          <p className="tnum mt-1 text-[12px] text-[var(--color-ink-dim)]">
            {site
              ? `${formatCoordinate(site.latitude_deg, site.longitude_deg)} · ${num(site.elevation_m, 0)} m · ${site.climate_objective.replace(/_/g, " ")}`
              : "Go back and choose a site."}
            {design ? ` · ${describeDesign(design)}, ${num(design.plan.floor_area_m2, 0)} m²` : ""}
            {` · simulated ${store.period.start} to ${store.period.end}`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => store.setPage("input")}
          className="rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] px-4 py-2 text-sm text-[var(--color-ink)] transition hover:border-[var(--color-solar)]"
        >
          ← Edit inputs
        </button>
      </div>

      <Tabs tabs={OUTPUT_TABS} active={tab} onChange={store.open} />

      {tab === "recommendation" && (
        <div className="grid items-start gap-5 xl:grid-cols-[minmax(360px,440px)_1fr]">
          <SearchPanel meta={meta} />
          <div className="xl:sticky xl:top-28">
            <Simulation
              viewportOnly
              preview={store.preview}
              geometry={store.preview ? previewGeometry : geometry}
              simulation={simulation}
              simulating={simulating}
              error={error}
            />
          </div>
        </div>
      )}
      {tab === "3d model" && (
        <Simulation geometry={geometry} simulation={simulation} simulating={simulating} error={error} />
      )}
      {tab === "climate" && <ClimateData />}
      {tab === "construction" && <DesignParameters />}
      {tab === "performance" && (
        <Results simulation={simulation} simulating={simulating} error={error} />
      )}
      {tab === "compare" && <Compare />}
      {tab === "report" && <Reports simulation={simulation} meta={meta} />}
    </div>
  );
}
