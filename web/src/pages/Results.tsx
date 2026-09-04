import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, isCfdAbsent, type SimulationResult } from "../lib/api";
import {
  AssumptionTable,
  Empty,
  Failure,
  Loading,
  Panel,
  Stat,
  Tabs,
  Warnings,
} from "../components/Kit";
import { PerformanceChart } from "../components/PerformanceChart";
import { ParetoExplorer } from "../components/Pareto";
import { activeDesign, useStore } from "../lib/store";
import { num, rupees, signed } from "../lib/format";

const TABS = [
  "summary",
  "materials",
  "heat flow",
  "pareto explorer",
  "assumptions",
  "validation",
  "detailed analysis",
] as const;
type Tab = (typeof TABS)[number];

function Recommendation({ result }: { result: SimulationResult }) {
  const cooling = result.headline_metric === "maximum_daytime_operative_c";
  const reference = result.reference;
  const top = result.recommendations?.[0];
  const baselineValue =
    reference?.delta_minimum_overnight_k != null &&
    result.comfort.minimum_overnight_operative_c != null
      ? result.comfort.minimum_overnight_operative_c - reference.delta_minimum_overnight_k
      : null;

  return (
    <div>
      <p className="display max-w-4xl text-xl leading-snug">
        {cooling ? (
          <>
            The design peaks at{" "}
            <span className="text-[var(--color-solar)]">
              {num(result.comfort.maximum_daytime_operative_c)} °C
            </span>{" "}
            through the day. Mass buffers the same large day to night swing as in Ladakh,
            with the sign flipped: it stores night coolth and releases it through the
            afternoon.
          </>
        ) : (
          <>
            This design holds{" "}
            <span className="text-[var(--color-solar)]">
              {num(result.comfort.minimum_overnight_operative_c)} °C
            </span>{" "}
            at its coldest overnight
            {baselineValue !== null && (
              <>
                , where current construction holds{" "}
                <span className="text-[var(--color-diesel)]">
                  {num(baselineValue)} °C
                </span>
              </>
            )}
            .
          </>
        )}
      </p>
      {top && (
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-ink-dim)]">
          {top.detail}
        </p>
      )}
    </div>
  );
}

export function Results({
  simulation,
  simulating,
  error,
}: {
  simulation: SimulationResult | undefined;
  simulating: boolean;
  error: unknown;
}) {
  const store = useStore();
  const design = activeDesign(store);
  const [tab, setTab] = useState<Tab>("summary");

  const materials = useQuery({
    queryKey: ["materials"],
    queryFn: api.materials,
    staleTime: Infinity,
  });
  const validation = useQuery({
    queryKey: ["validation"],
    queryFn: api.validation,
    enabled: tab === "validation",
    staleTime: Infinity,
  });
  const cfd = useQuery({
    queryKey: ["cfd", simulation?.design_fingerprint],
    queryFn: () => api.cfd(simulation!.design_fingerprint),
    enabled: Boolean(simulation?.design_fingerprint) && tab === "detailed analysis",
    staleTime: Infinity,
  });

  if (error) return <Failure error={error} />;
  if (!simulation) return <Loading label={simulating ? "running EnergyPlus…" : "waiting…"} />;

  const comfort = simulation.comfort;
  const reference = simulation.reference;
  const cooling = simulation.headline_metric === "maximum_daytime_operative_c";
  const bands = new Map((simulation.uncertainty ?? []).map((item) => [item.metric, item]));
  const overnightBand = bands.get("minimum_overnight_operative_c");
  const fuelSaved = reference?.fuel_saved_units ?? null;
  const showFuel = !cooling && fuelSaved !== null && Math.abs(fuelSaved) > 0.5;
  const rupeesSaved = reference?.rupees_saved_per_year ?? null;
  const shares = simulation.breakdown;

  const lossPaths: [string, number][] = (
    [
      ["Walls", -shares.walls_kwh],
      ["Roof", -shares.roof_kwh],
      ["Floor", -shares.floor_kwh],
      ["Windows, conduction", -shares.windows_conduction_kwh],
      ["Infiltration", -shares.infiltration_kwh],
      ["Ventilation", -shares.ventilation_kwh],
      ["Between zones", -shares.interzone_kwh],
      ["Thermal bridges", shares.thermal_bridge_kwh],
    ] as [string, number][]
  )
    .filter((entry) => entry[1] > 0)
    .sort((a, b) => b[1] - a[1]);
  const lossTotal = lossPaths.reduce((sum, [, value]) => sum + value, 0);

  return (
    <div className="space-y-4">
      <Panel>
        <Recommendation result={simulation} />
      </Panel>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {showFuel ? (
          <Stat
            label={`${reference?.fuel_unit_name ?? "unit"}s of fuel avoided`}
            value={num(fuelSaved, 0)}
            unit={`${reference?.fuel_unit_name ?? ""} over the period`}
            tone="solar"
            large
            note={
              rupeesSaved != null && rupeesSaved > 0
                ? `${rupees(rupeesSaved)} of fuel cost`
                : "Fuel price is not set, so the rupee figure is withheld rather than invented"
            }
          />
        ) : null}
        <Stat
          label={cooling ? "Peak daytime operative" : "Minimum overnight operative"}
          value={num(
            cooling
              ? comfort.maximum_daytime_operative_c
              : comfort.minimum_overnight_operative_c,
          )}
          unit="°C"
          large
          tone={showFuel ? "neutral" : "solar"}
          band={
            overnightBand && overnightBand.high > overnightBand.low
              ? `${num(overnightBand.low)} to ${num(overnightBand.high)} °C, driven mainly by ${overnightBand.dominant_parameter?.toLowerCase() ?? "input uncertainty"}`
              : undefined
          }
          baseline={
            reference?.delta_minimum_overnight_k != null
              ? `${signed(reference.delta_minimum_overnight_k)} K against`
              : undefined
          }
        />
        <Stat
          label="Hours below the comfort band"
          value={num(comfort.hours_below_band, 0)}
          unit="h"
          baseline={
            reference?.delta_degree_hours_kh != null
              ? `${signed(reference.delta_degree_hours_kh, 0)} Kh against`
              : undefined
          }
        />
        <Stat
          label={cooling ? "Remaining cooling demand" : "Remaining heating demand"}
          value={num(
            cooling
              ? simulation.energy.cooling_demand_kwh
              : simulation.energy.heating_demand_kwh,
            0,
          )}
          unit="kWh"
          baseline={
            reference?.delta_heating_demand_kwh != null
              ? `${signed(reference.delta_heating_demand_kwh, 0)} kWh against`
              : undefined
          }
        />
      </div>

      <Panel
        title="Performance over time"
        subtitle="Operative temperature, not air temperature. The faint red line is what current construction does under the same weather, the same occupancy and the same period."
      >
        <PerformanceChart result={simulation} />
      </Panel>

      <Panel>
        <Tabs tabs={TABS} active={tab} onChange={setTab} />
        <div className="mt-3">
          {tab === "summary" && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat
                label="Envelope material cost"
                value={rupees(simulation.cost.total_inr)}
                note={`${simulation.cost.max_mason_skill} labour · ${num(
                  simulation.cost.non_local_material_fraction * 100,
                  0,
                )} % imported`}
                baseline={
                  reference?.delta_cost_inr != null
                    ? `${signed(reference.delta_cost_inr, 0)} INR against`
                    : undefined
                }
              />
              <Stat
                label="Condensation hours on glazing"
                value={num(simulation.condensation.hours, 0)}
                unit="h"
                note={
                  simulation.condensation.worst_surface
                    ? `worst at ${simulation.condensation.worst_surface}`
                    : undefined
                }
              />
              <Stat
                label="Ventilation"
                value={simulation.buildability.minimum_ventilation_met ? "Met" : "Not met"}
                note="a health constraint that was satisfied, never a score"
                tone={simulation.buildability.minimum_ventilation_met ? "good" : "bad"}
              />
              <Stat
                label="Indoor swing against outdoor"
                value={num(comfort.decrement_factor, 2)}
                note={
                  comfort.time_lag_h != null
                    ? `peak arrives ${num(Math.abs(comfort.time_lag_h), 1)} h ${
                        comfort.time_lag_h >= 0 ? "after" : "before"
                      } outdoors`
                    : undefined
                }
              />
              <div className="sm:col-span-2 lg:col-span-4">
                <h3 className="mb-2 text-sm font-medium">
                  Recommendations, generated from the numbers above
                </h3>
                <ol className="space-y-2.5">
                  {simulation.recommendations.map((item) => (
                    <li
                      key={item.code}
                      className="border-l-2 border-[var(--color-line)] pl-3"
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                            item.priority === "high"
                              ? "bg-[var(--color-diesel)]/20 text-[#ffb4ae]"
                              : item.priority === "medium"
                                ? "bg-[var(--color-solar)]/15 text-[var(--color-solar)]"
                                : "bg-[var(--color-line)] text-[var(--color-ink-faint)]"
                          }`}
                        >
                          {item.priority}
                        </span>
                        <span className="text-sm font-medium">{item.title}</span>
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-[var(--color-ink-dim)]">
                        {item.detail}
                      </p>
                      <p className="mt-1 font-mono text-[10px] text-[var(--color-ink-faint)]">
                        {Object.entries(item.triggered_by)
                          .map(([key, value]) => `${key}=${num(value as number, 3)}`)
                          .join("  ")}
                      </p>
                    </li>
                  ))}
                </ol>
              </div>
            </div>
          )}

          {tab === "materials" && design && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-[var(--color-ink-faint)]">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Material</th>
                    <th className="py-1 pr-3 text-right font-medium">k (W/mK)</th>
                    <th className="py-1 pr-3 text-right font-medium">ρ (kg/m³)</th>
                    <th className="py-1 pr-3 text-right font-medium">cost (₹/m³)</th>
                    <th className="py-1 pr-3 font-medium">local</th>
                    <th className="py-1 pr-3 font-medium">skill</th>
                    <th className="py-1 font-medium">source</th>
                  </tr>
                </thead>
                <tbody>
                  {(materials.data ?? []).map((item) => (
                    <tr key={item.id} className="border-t border-[var(--color-line-soft)]">
                      <td className="py-1.5 pr-3">
                        <span className="flex items-center gap-2">
                          <span
                            className="inline-block h-2.5 w-2.5 rounded-sm"
                            style={{ background: item.appearance.colour }}
                          />
                          {item.name}
                        </span>
                      </td>
                      <td className="tnum py-1.5 pr-3 text-right">
                        {num(item.conductivity_w_mk, 3)}
                      </td>
                      <td className="tnum py-1.5 pr-3 text-right">
                        {num(item.density_kg_m3, 0)}
                      </td>
                      <td className="tnum py-1.5 pr-3 text-right">
                        {num(item.cost_inr_m3, 0)}
                      </td>
                      <td className="py-1.5 pr-3">
                        {item.locally_available ? "yes" : `×${item.transport_multiplier}`}
                      </td>
                      <td className="py-1.5 pr-3">{item.mason_skill}</td>
                      <td className="py-1.5 text-[var(--color-ink-faint)]">
                        <span
                          className={`mr-1.5 rounded px-1 py-0.5 text-[10px] ${
                            item.source.status === "verified"
                              ? "bg-[var(--color-good)]/20 text-[var(--color-good)]"
                              : "bg-[var(--color-solar)]/15 text-[var(--color-solar)]"
                          }`}
                        >
                          {item.source.status}
                        </span>
                        {item.source.citation}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {tab === "heat flow" && (
            <div className="space-y-2">
              {lossPaths.map(([label, value]) => (
                <div key={label} className="flex items-center gap-3 text-xs">
                  <span className="w-44 shrink-0 text-[var(--color-ink-dim)]">{label}</span>
                  <span className="h-3 flex-1 overflow-hidden rounded bg-[var(--color-night)]">
                    <span
                      className="block h-full rounded bg-[var(--color-cold)]"
                      style={{ width: `${(value / lossTotal) * 100}%` }}
                    />
                  </span>
                  <span className="tnum w-16 text-right">
                    {num((value / lossTotal) * 100, 1)} %
                  </span>
                  <span className="tnum w-20 text-right text-[var(--color-ink-faint)]">
                    {num(value, 0)} kWh
                  </span>
                </div>
              ))}
              <p className="pt-2 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
                Solar gain through the glazing over the same period was{" "}
                <span className="text-[var(--color-solar)]">
                  {num(shares.windows_solar_gain_kwh, 0)} kWh
                </span>
                , and internal gains from people and equipment{" "}
                {num(shares.internal_gains_kwh, 0)} kWh.
                {shares.thermal_bridge_kwh <= 0 &&
                  " No thermal bridge line appears because Layer 1 has not supplied a junction calculation yet; it is absent rather than assumed to be zero."}
              </p>
            </div>
          )}

          {tab === "pareto explorer" && <ParetoExplorer />}

          {tab === "assumptions" && (
            <AssumptionTable assumptions={simulation.assumptions} />
          )}

          {tab === "validation" && (
            <div className="space-y-3">
              {!validation.data ? (
                <Loading label="looking for committed evidence…" />
              ) : validation.data.available ? (
                <pre className="max-h-96 overflow-auto rounded-lg bg-[var(--color-night)] p-3 text-[11px]">
                  {JSON.stringify(validation.data, null, 2)}
                </pre>
              ) : (
                <Empty
                  title="No validation evidence is committed yet"
                  detail={validation.data.detail}
                />
              )}
              <Warnings warnings={simulation.warnings} />
            </div>
          )}

          {tab === "detailed analysis" && (
            <div className="space-y-3">
              {!cfd.data ? (
                <Loading label="checking for a stored field…" />
              ) : isCfdAbsent(cfd.data) ? (
                <Empty
                  title="Detailed analysis has not been run for this design"
                  detail={cfd.data.reason}
                />
              ) : (
                <>
                  <div className="rounded-md border border-[var(--color-line)] bg-[var(--color-night)] p-3 text-xs leading-relaxed">
                    <span className="font-medium">
                      Boundary conditions for {cfd.data.label.replace(/_/g, " ")}
                    </span>{" "}
                    at {cfd.data.timestamp.replace("T", " ")}. Zone air{" "}
                    {num(cfd.data.zone_air_temperature_c)} °C, outdoor{" "}
                    {num(cfd.data.outdoor_air_temperature_c)} °C, volume{" "}
                    {num(cfd.data.zone_volume_m3)} m³.
                  </div>
                  <table className="w-full text-left text-xs">
                    <thead className="text-[var(--color-ink-faint)]">
                      <tr>
                        <th className="py-1 pr-3 font-medium">Surface</th>
                        <th className="py-1 pr-3 text-right font-medium">Inside face °C</th>
                        <th className="py-1 pr-3 text-right font-medium">Area m²</th>
                        <th className="py-1 text-right font-medium">Emissivity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cfd.data.surfaces.map((surface) => (
                        <tr
                          key={surface.name}
                          className="border-t border-[var(--color-line-soft)]"
                        >
                          <td className="py-1.5 pr-3 font-mono">{surface.name}</td>
                          <td className="tnum py-1.5 pr-3 text-right">
                            {num(surface.inside_face_temperature_c)}
                          </td>
                          <td className="tnum py-1.5 pr-3 text-right">
                            {num(surface.area_m2, 2)}
                          </td>
                          <td className="tnum py-1.5 text-right">
                            {num(surface.emissivity, 2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <ul className="space-y-1 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
                    {cfd.data.notes.map((note, index) => (
                      <li key={index}>· {note}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}
