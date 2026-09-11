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
  const isBaseline =
    reference != null &&
    result.design.name === reference.reference_name;

  return (
    <div className="py-2">
      <p className="display max-w-4xl text-[28px] font-bold tracking-tight leading-snug text-gray-900">
        {cooling ? (
          <>
            The design peaks at{" "}
            <span className="text-[var(--color-solar)]">
              {num(result.comfort.maximum_daytime_operative_c)} °C
            </span>{" "}
            through the day.
          </>
        ) : (
          <>
            This design holds{" "}
            <span className="text-[var(--color-solar)]">
              {num(result.comfort.minimum_overnight_operative_c)} °C
            </span>{" "}
            at its coldest overnight
            {isBaseline ? (
              " . This is current construction itself, so there is nothing to"
              + " compare it against. Choose a candidate design to see a difference."
            ) : baselineValue !== null ? (
              <>
                , where current construction holds{" "}
                <span className="text-rose-500">
                  {num(baselineValue)} °C
                </span>
                .
              </>
            ) : (
              "."
            )}
          </>
        )}
      </p>
      {top && (
        <p className="mt-4 max-w-3xl text-[15px] leading-relaxed text-gray-500 font-medium border-l-[3px] border-[var(--color-solar)] pl-4">
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

      <Panel title="Performance over time">
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
              <div className="sm:col-span-2 lg:col-span-4 mt-4">
                <h3 className="mb-4 text-[15px] font-bold tracking-tight text-slate-800">
                  Recommendations based on the simulation
                </h3>
                <div className="space-y-3">
                  {simulation.recommendations.map((item) => (
                    <div
                      key={item.code}
                      className="group rounded-2xl border border-slate-100 bg-slate-50/50 p-4 transition-all hover:bg-slate-50 hover:shadow-sm"
                    >
                      <div className="flex items-center gap-3">
                        <span
                          className={`rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest ${
                            item.priority === "high"
                              ? "bg-rose-100 text-rose-700"
                              : item.priority === "medium"
                                ? "bg-amber-100 text-amber-700"
                                : "bg-slate-200/50 text-slate-600"
                          }`}
                        >
                          {item.priority}
                        </span>
                        <span className="text-[15px] font-semibold text-slate-800">{item.title}</span>
                      </div>
                      <p className="mt-2 pl-[4.5rem] text-sm leading-relaxed text-slate-600">
                        {item.detail}
                      </p>
                      <p className="mt-1.5 pl-[4.5rem] font-mono text-[11px] text-slate-400">
                        {Object.entries(item.triggered_by)
                          .map(([key, value]) => `${key}=${num(value as number, 3)}`)
                          .join("  ")}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {tab === "materials" && design && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 border-b border-slate-200">
                  <tr>
                    <th className="py-3 pr-3 font-semibold">Material</th>
                    <th className="py-3 pr-3 text-right font-semibold">k (W/mK)</th>
                    <th className="py-3 pr-3 text-right font-semibold">ρ (kg/m³)</th>
                    <th className="py-3 pr-3 text-right font-semibold">cost (₹/m³)</th>
                    <th className="py-3 pr-3 font-semibold">local</th>
                    <th className="py-3 pr-3 font-semibold">skill</th>
                    <th className="py-3 font-semibold">source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {(materials.data ?? []).map((item) => (
                    <tr key={item.id} className="hover:bg-slate-50/50 transition-colors">
                      <td className="py-2.5 pr-3">
                        <span className="flex items-center gap-2 font-medium text-slate-800">
                          <span
                            className="inline-block h-3 w-3 rounded-full shadow-sm"
                            style={{ background: item.appearance.colour }}
                          />
                          {item.name}
                        </span>
                      </td>
                      <td className="tnum py-2.5 pr-3 text-right text-slate-600">
                        {num(item.conductivity_w_mk, 3)}
                      </td>
                      <td className="tnum py-2.5 pr-3 text-right text-slate-600">
                        {num(item.density_kg_m3, 0)}
                      </td>
                      <td className="tnum py-2.5 pr-3 text-right text-slate-600">
                        {num(item.cost_inr_m3, 0)}
                      </td>
                      <td className="py-2.5 pr-3 text-slate-600">
                        {item.locally_available ? "yes" : `×${item.transport_multiplier}`}
                      </td>
                      <td className="py-2.5 pr-3 text-slate-600">{item.mason_skill}</td>
                      <td className="py-2.5 text-slate-500 text-xs">
                        <span
                          className={`mr-2 inline-block rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest ${
                            item.source.status === "verified"
                              ? "bg-emerald-100 text-emerald-700"
                              : "bg-amber-100 text-amber-700"
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
                <div key={label} className="grid grid-cols-[1fr_2fr_auto_auto] sm:grid-cols-[11rem_1fr_4rem_5rem] items-center gap-3 text-xs">
                  <span className="text-[var(--color-ink-dim)] truncate" title={label}>{label}</span>
                  <span className="h-3 overflow-hidden rounded bg-[var(--color-night)]">
                    <span
                      className="block h-full rounded bg-[var(--color-cold)] transition-all duration-1000 ease-out"
                      style={{ width: `${(value / lossTotal) * 100}%` }}
                    />
                  </span>
                  <span className="tnum text-right">
                    {num((value / lossTotal) * 100, 1)} %
                  </span>
                  <span className="tnum text-right text-[var(--color-ink-faint)]">
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
                  <table className="w-full text-left text-sm mt-4">
                    <thead className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 border-b border-slate-200">
                      <tr>
                        <th className="py-3 pr-3 font-semibold">Surface</th>
                        <th className="py-3 pr-3 text-right font-semibold">Inside face °C</th>
                        <th className="py-3 pr-3 text-right font-semibold">Area m²</th>
                        <th className="py-3 text-right font-semibold">Emissivity</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {cfd.data.surfaces.map((surface) => (
                        <tr
                          key={surface.name}
                          className="hover:bg-slate-50/50 transition-colors"
                        >
                          <td className="py-2.5 pr-3 font-mono text-[13px] text-slate-700">{surface.name}</td>
                          <td className="tnum py-2.5 pr-3 text-right font-medium text-slate-800">
                            {num(surface.inside_face_temperature_c)}
                          </td>
                          <td className="tnum py-2.5 pr-3 text-right text-slate-600">
                            {num(surface.area_m2, 2)}
                          </td>
                          <td className="tnum py-2.5 text-right text-slate-600">
                            {num(surface.emissivity, 2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <ul className="space-y-1 text-xs leading-relaxed text-[var(--color-ink-faint)]">
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
