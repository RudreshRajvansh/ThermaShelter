import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type SimulationResult } from "../lib/api";
import { Failure, Loading, Panel, Stat } from "./Kit";
import { useStore } from "../lib/store";
import { num, signed } from "../lib/format";

const LOSS_LABEL: Record<string, string> = {
  walls: "walls",
  roof: "roof",
  floor: "floor",
  windows_conduction: "windows",
  infiltration: "leakage",
  ventilation: "fresh air",
  interzone: "between zones",
  thermal_bridge: "thermal bridges",
};

function biggestLoss(result: SimulationResult): { path: string; share: number } | null {
  const b = result.breakdown;
  const parts: [string, number][] = [
    ["walls", -b.walls_kwh],
    ["roof", -b.roof_kwh],
    ["floor", -b.floor_kwh],
    ["windows_conduction", -b.windows_conduction_kwh],
    ["infiltration", -b.infiltration_kwh],
    ["ventilation", -b.ventilation_kwh],
    ["interzone", -b.interzone_kwh],
    ["thermal_bridge", b.thermal_bridge_kwh],
  ];
  const positive = parts.filter(([, value]) => value > 0);
  const total = positive.reduce((sum, [, value]) => sum + value, 0);
  if (total <= 0) return null;
  const [path, value] = positive.sort((a, b2) => b2[1] - a[1])[0];
  return { path: LOSS_LABEL[path] ?? path, share: value / total };
}

export function DrdoOutputs({ result }: { result: SimulationResult }) {
  const comfort = result.comfort;
  const loss = biggestLoss(result);
  const drdo = comfort.comfort_model_id === "drdo_tot_band";
  return (
    <Panel
      title="DRDO outputs"
      subtitle="The three outputs the problem statement asks for, each read straight from the EnergyPlus run above."
    >
      <div className="grid gap-3 md:grid-cols-3">
        <Stat
          label="Output 1 · inside temperature"
          value={num(comfort.minimum_overnight_operative_c, 1)}
          unit="°C at the coldest night hour"
          tone="solar"
          note={
            drdo
              ? `Judged against DRDO's 17 ± 3 °C band. ${num(comfort.fixed_target_hours_below, 0)} night hours below 14 °C, ${num(comfort.fixed_target_degree_hours_below_kh, 0)} Kh in all.`
              : `Judged against ${comfort.comfort_model_id}, not DRDO's 17 ± 3 °C band.`
          }
        />
        <Stat
          label="Output 2 · solar energy captured"
          value={num(result.breakdown.windows_solar_gain_kwh, 0)}
          unit="kWh through the glass"
          note="Transmitted solar gain summed over the simulated period."
        />
        <Stat
          label="Output 3 · heat flow by path"
          value={loss ? loss.path : "—"}
          unit={loss ? `${num(loss.share * 100, 0)} % of the losses` : undefined}
          note="The largest path out of the room. The heat flow tab below breaks down every path."
        />
      </div>
    </Panel>
  );
}

export function BreatheSafe({ result }: { result: SimulationResult }) {
  const air = result.air_quality;
  if (!air) {
    return (
      <Panel title="Breathe-safe">
        <p className="text-sm text-[var(--color-ink-dim)]">
          Nobody sleeps in this shelter, so there is no CO₂ to hold down.
        </p>
      </Panel>
    );
  }
  const safe = air.night_hours_above_limit <= 0;
  return (
    <Panel
      title="Breathe-safe"
      subtitle={`At ${num(air.site_pressure_kpa, 1)} kPa the same breath fills more volume than at sea level, so a sealed shelter goes stale faster up here.`}
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Fresh air per person"
          value={num(air.fresh_air_l_s_per_person, 1)}
          unit="L/s"
          note={`${num(air.sea_level_fresh_air_l_s_per_person, 1)} L/s would do at sea level. ${air.fresh_air_sized_from_limit ? `Sized to hold ${num(air.co2_limit_ppm, 0)} ppm.` : "Set on the design."}`}
        />
        <Stat
          label="Night CO₂"
          value={num(air.peak_night_co2_ppm, 0)}
          unit="ppm at its highest"
          tone={safe ? "good" : "bad"}
          note={`mean ${num(air.mean_night_co2_ppm, 0)} ppm against a ${num(air.co2_limit_ppm, 0)} ppm limit and ${num(air.outdoor_co2_ppm, 0)} ppm outdoors`}
        />
        <Stat
          label="Night hours over the limit"
          value={num(air.night_hours_above_limit, 0)}
          unit="h"
          tone={safe ? "good" : "bad"}
        />
        <Stat
          label="Heat the fresh air costs"
          value={num(air.ventilation_heat_loss_kwh, 0)}
          unit="kWh over the period"
          note={`${num(air.ventilation_design_l_s, 0)} L/s of ventilation on top of ${num(air.infiltration_design_l_s, 0)} L/s of leakage while occupied`}
        />
      </div>
      <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-ink-faint)]">
        The model tracks CO₂ only. It does not compute carbon monoxide from a heater, so a
        post burning kerosene still needs a CO alarm.
      </p>
    </Panel>
  );
}

export function FuelOut({ result }: { result: SimulationResult }) {
  const store = useStore();
  const [asked, setAsked] = useState(false);
  const run = useQuery({
    queryKey: ["endurance", result.design_hash, store.siteId],
    queryFn: () => api.endurance({ design: result.design, site_id: store.siteId }),
    enabled: asked,
    staleTime: Infinity,
    retry: false,
  });
  const data = run.data;
  const spare = data?.leftover_heat;
  return (
    <Panel
      title="If the kerosene is late"
      subtitle="The shelter heated to 14 °C each night of the site's 1 in 10 design week, then left without fuel from 18:00 on the design night."
      right={
        !asked ? (
          <button
            type="button"
            onClick={() => setAsked(true)}
            className="rounded-lg bg-[var(--color-solar)] px-3 py-1.5 text-sm font-medium text-white"
          >
            Run on the design night
          </button>
        ) : undefined
      }
    >
      {!asked && (
        <p className="text-sm text-[var(--color-ink-dim)]">
          Two EnergyPlus runs on the site's design night, about ten seconds.
        </p>
      )}
      {asked && run.isFetching && <Loading label="finding the design night and running it…" />}
      {run.error && <Failure error={run.error} />}
      {data && spare && (
        <div className="space-y-3">
          <p className="text-sm text-[var(--color-ink-dim)]">
            Design night {data.night_of}: {num(data.design_night_minimum_c, 1)} °C outside,
            against a 1 in {num(data.return_period_years, 0)} year level of{" "}
            {num(data.return_level_c, 1)} °C.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label={`Hours above ${num(data.floor_c, 0)} °C without fuel`}
              value={data.reached_floor ? num(data.hours_above_floor, 0) : `> ${num(data.hours_simulated_after_cutoff, 0)}`}
              unit="h"
              tone={data.reached_floor && data.hours_above_floor < 12 ? "bad" : "good"}
              note={`coldest ${num(data.minimum_after_cutoff_c, 1)} °C at ${data.minimum_at.replace("T", " ").slice(0, 16)}`}
            />
            <Stat
              label="Heat the design night needs"
              value={num(spare.night_heating_kwh, 1)}
              unit="kWh"
              note={spare.night_peak_kw != null ? `peaking at ${num(spare.night_peak_kw, 1)} kW` : undefined}
            />
            <Stat
              label="Kerosene tonight"
              value={num(spare.litres, 1)}
              unit="L"
              note={
                spare.him_tapak_hours_low != null
                  ? `${num(spare.him_tapak_hours_low, 1)} to ${num(spare.him_tapak_hours_high, 1)} h of Him Tapak`
                  : undefined
              }
            />
            <Stat
              label="Solar storage for one night"
              value={num(spare.solar_storage_kwh, 1)}
              unit="kWh"
              note="What a DIHAR solar system would have to hold; its capacity is not published."
            />
          </div>
          {(spare.notes ?? []).map((note) => (
            <p key={note} className="text-[12px] text-[var(--color-warn-ink)]">
              {note}
            </p>
          ))}
          <p className="text-[12px] leading-relaxed text-[var(--color-ink-faint)]">
            {data.floor_source.citation}. Weather: {data.weather_source}.
          </p>
        </div>
      )}
    </Panel>
  );
}

export function SkylineValue({ result }: { result: SimulationResult }) {
  const store = useStore();
  const [asked, setAsked] = useState(false);
  const run = useQuery({
    queryKey: ["horizon-value", result.design_hash, store.siteId, store.period],
    queryFn: () =>
      api.horizonValue({ design: result.design, site_id: store.siteId, period: store.period }),
    enabled: asked,
    staleTime: Infinity,
    retry: false,
  });
  const data = run.data;
  return (
    <Panel
      title="What the mountains cost"
      subtitle="The same design and week run twice: once shaded by the measured skyline, once under a flat horizon."
      right={
        !asked ? (
          <button
            type="button"
            onClick={() => setAsked(true)}
            className="rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] px-3 py-1.5 text-sm"
          >
            Compare with a flat horizon
          </button>
        ) : undefined
      }
    >
      {asked && run.isFetching && <Loading label="running with and without the skyline…" />}
      {run.error && <Failure error={run.error} />}
      {data && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat
            label="Coldest night hour"
            value={signed(data.overnight_minimum_difference_k, 2)}
            unit="K against a flat horizon"
            tone={(data.overnight_minimum_difference_k ?? 0) < -1 ? "bad" : "neutral"}
            note={`${num(data.with_horizon.minimum_overnight_operative_c, 1)} °C with the skyline, ${num(data.flat.minimum_overnight_operative_c, 1)} °C flat`}
          />
          <Stat
            label="Extra heating"
            value={signed(data.heating_difference_kwh, 0)}
            unit="kWh"
            note={`${signed(data.fuel_difference_units, 1)} ${data.with_horizon.fuel_unit_name ?? "units"}`}
          />
          <Stat
            label="Skyline"
            value={num(data.max_blocked_altitude_deg, 1)}
            unit="° at its highest"
            note="A tool that assumed a flat horizon would promise the difference on the left."
          />
        </div>
      )}
    </Panel>
  );
}
