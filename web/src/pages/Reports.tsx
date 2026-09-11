import { useQuery } from "@tanstack/react-query";
import { api, type MetaResponse, type SimulationResult } from "../lib/api";
import { AssumptionTable, Empty, Loading, Panel, Warnings } from "../components/Kit";
import { PerformanceChart } from "../components/PerformanceChart";
import { activeDesign, useStore } from "../lib/store";
import { num, rupees, signed } from "../lib/format";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-[var(--color-line-soft)] py-1.5 text-xs">
      <span className="text-[var(--color-ink-faint)]">{label}</span>
      <span className="tnum text-right">{value}</span>
    </div>
  );
}

export function Reports({
  simulation,
  meta,
}: {
  simulation: SimulationResult | undefined;
  meta: MetaResponse | undefined;
}) {
  const store = useStore();
  const design = activeDesign(store);
  const climate = useQuery({
    queryKey: ["climate", store.siteId],
    queryFn: () => api.climate(store.siteId),
    enabled: Boolean(store.siteId),
    staleTime: Infinity,
  });
  const validation = useQuery({
    queryKey: ["validation"],
    queryFn: api.validation,
    staleTime: Infinity,
  });

  if (!simulation || !design) {
    return (
      <Empty
        title="Run a simulation first"
        detail="A report is assembled from a completed run. Go back to the simulation step, let EnergyPlus finish, then return here."
      />
    );
  }

  const reference = simulation.reference;
  const cooling = simulation.headline_metric === "maximum_daytime_operative_c";
  const site = meta?.sites.find((item) => item.id === store.siteId);
  const band = simulation.uncertainty.find(
    (item) => item.metric === "minimum_overnight_operative_c",
  );

  return (
    <div className="space-y-4">
      <div className="no-print flex items-center justify-between">
        <p className="text-xs text-[var(--color-ink-faint)]">
          Everything below is assembled from the completed run. Print to PDF from the
          browser; the layout switches to light for paper.
        </p>
        <button
          type="button"
          onClick={() => window.print()}
          className="rounded-md bg-[var(--color-solar)] px-3 py-1.5 text-xs font-medium text-[#0b1220]"
        >
          Print or save as PDF
        </button>
      </div>

      <Panel title="Executive summary">
        <p className="display max-w-4xl text-lg leading-snug">
          {cooling ? (
            <>
              At {site?.name ?? store.siteId} the recommended design peaks at{" "}
              {num(simulation.comfort.maximum_daytime_operative_c)} °C through the day.
            </>
          ) : (
            simulation.design.name === reference?.reference_name ? (
              <>
                At {site?.name ?? store.siteId} current construction holds{" "}
                {num(simulation.comfort.minimum_overnight_operative_c)} °C at its
                coldest overnight. This report describes the baseline itself, so it
                carries no improvement. Run a candidate design to report a difference
                against it.
              </>
            ) : (
              <>
                At {site?.name ?? store.siteId} the recommended design holds{" "}
                {num(simulation.comfort.minimum_overnight_operative_c)} °C at its coldest
                overnight against{" "}
                {reference?.delta_minimum_overnight_k != null &&
                simulation.comfort.minimum_overnight_operative_c != null
                  ? num(
                      simulation.comfort.minimum_overnight_operative_c -
                        reference.delta_minimum_overnight_k,
                    )
                  : "—"}{" "}
                °C for current construction, a difference of{" "}
                {signed(reference?.delta_minimum_overnight_k)} K.
              </>
            )
          )}{" "}
          {reference?.fuel_saved_units != null && reference.fuel_saved_units > 0 && (
            <>
              Over the simulated period that avoids {num(reference.fuel_saved_units, 0)}{" "}
              {reference.fuel_unit_name} of {simulation.energy.fuel}.
            </>
          )}
        </p>
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-ink-faint)]">
              Result
            </h3>
            <Row
              label={cooling ? "Peak daytime operative" : "Minimum overnight operative"}
              value={`${num(
                cooling
                  ? simulation.comfort.maximum_daytime_operative_c
                  : simulation.comfort.minimum_overnight_operative_c,
              )} °C`}
            />
            {band && (
              <Row
                label="Uncertainty band"
                value={`${num(band.low)} to ${num(band.high)} °C, ${
                  band.dominant_parameter ?? "—"
                }`}
              />
            )}
            <Row
              label="Hours below the comfort band"
              value={`${num(simulation.comfort.hours_below_band, 0)} h`}
            />
            <Row
              label="Degree hours outside the band"
              value={`${num(simulation.comfort.degree_hours_total_kh, 0)} Kh`}
            />
            <Row
              label="Remaining heating demand"
              value={`${num(simulation.energy.heating_demand_kwh, 0)} kWh`}
            />
            <Row
              label="Fuel"
              value={
                simulation.energy.fuel_units != null
                  ? `${num(simulation.energy.fuel_units, 0)} ${simulation.energy.fuel_unit_name}`
                  : "not computed"
              }
            />
            <Row
              label="Fuel cost"
              value={
                simulation.energy.fuel_cost_inr
                  ? rupees(simulation.energy.fuel_cost_inr)
                  : "withheld, fuel price not set"
              }
            />
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-ink-faint)]">
              Site and climate
            </h3>
            {climate.data ? (
              <>
                <Row label="Location" value={climate.data.name} />
                <Row
                  label="Position"
                  value={`${num(climate.data.latitude_deg, 2)}° N, ${num(
                    climate.data.longitude_deg,
                    2,
                  )}° E, ${num(climate.data.elevation_m, 0)} m`}
                />
                <Row label="Weather file" value={climate.data.weather_file} />
                <Row
                  label="Heating degree days"
                  value={`${num(climate.data.heating_degree_days, 0)} base 18 °C`}
                />
                <Row
                  label="Annual solar"
                  value={`${num(climate.data.annual_global_horizontal_kwh_m2, 0)} kWh/m²`}
                />
                <Row
                  label="Design day"
                  value={`${climate.data.design_day}, min ${num(
                    climate.data.design_day_min_c,
                  )} °C`}
                />
                <Row
                  label="Daylight lost to terrain"
                  value={`${num(climate.data.sun_path.daylight_lost_to_terrain_h, 1)} h`}
                />
              </>
            ) : (
              <Loading label="loading climate…" />
            )}
          </div>
        </div>
      </Panel>

      <Panel title="Performance over time">
        <PerformanceChart result={simulation} />
      </Panel>

      <Panel title="Construction specification">
        <div className="grid gap-4 lg:grid-cols-2">
          {(
            [
              ["Wall", design.wall],
              ["Roof", design.roof],
              ["Floor", design.floor],
              ["Partition", design.partition],
            ] as const
          ).map(([label, construction]) =>
            construction ? (
              <div key={label}>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-ink-faint)]">
                  {label} — {construction.name}
                </h3>
                {construction.layers.map((layer, index) => (
                  <Row
                    key={`${layer.material_id}-${index}`}
                    label={`${index + 1}. ${layer.material_id}`}
                    value={`${num(layer.thickness_m * 1000, 0)} mm`}
                  />
                ))}
              </div>
            ) : null,
          )}
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-ink-faint)]">
              Openings and air
            </h3>
            {Object.entries(design.windows ?? {}).map(([facade, window]) => (
              <Row
                key={facade}
                label={`${facade} glazing`}
                value={`${num(window.wwr * 100, 0)} % of wall, ${window.glazing_id}${
                  window.shutter.present ? ", shuttered" : ""
                }`}
              />
            ))}
            <Row
              label="Infiltration"
              value={`${num(design.ventilation.infiltration_ach, 2)} ACH`}
            />
            <Row
              label="Fresh air floor"
              value={`${num(design.ventilation.minimum_fresh_air_ach, 2)} ACH`}
            />
            <Row label="Occupants" value={String(design.occupancy.occupants)} />
            <Row
              label="Wall thickness"
              value={`${num(simulation.buildability.wall_thickness_m * 1000, 0)} mm`}
            />
            <Row
              label="Envelope material cost"
              value={`${rupees(simulation.cost.total_inr)}, ${simulation.cost.max_mason_skill} labour`}
            />
          </div>
        </div>
        {simulation.buildability.seismic_note && (
          <p className="mt-3 rounded-md border border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 p-3 text-xs text-[#ffb4ae]">
            {simulation.buildability.seismic_note}
          </p>
        )}
      </Panel>

      <Panel
        title="Assumptions"
        subtitle="Every input this result rests on, with the source it came from."
      >
        <AssumptionTable assumptions={simulation.assumptions} />
      </Panel>

      <Panel
        title="Validation"
        subtitle="Standard test cases, the ANSYS junction calculation and any calibration, committed as files by Layer 1."
      >
        {validation.data?.available ? (
          <pre className="max-h-80 overflow-auto rounded-lg bg-[var(--color-night)] p-3 text-[11px]">
            {JSON.stringify(validation.data, null, 2)}
          </pre>
        ) : (
          <Empty
            title="No validation evidence is committed yet"
            detail={
              validation.data?.detail ??
              "Layer 1 owns this. Until those files exist the report says so rather than implying the engine has been validated."
            }
          />
        )}
      </Panel>

      <Panel
        title="What this run reported about itself"
        subtitle="Included in the report deliberately. A tool that hides its own warnings will eventually present a broken result on stage."
      >
        <Warnings warnings={simulation.warnings} collapsedInfo={false} />
      </Panel>

      <p className="text-[11px] text-[var(--color-ink-faint)]">
        Engine {simulation.engine_version}, EnergyPlus {simulation.energyplus_version},
        design {simulation.design_fingerprint}, run {simulation.design_hash}.
      </p>
    </div>
  );
}
