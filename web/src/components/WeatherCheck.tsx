import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import { Failure, Loading, Panel, Stat } from "./Kit";
import { num } from "../lib/format";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
  "Dec"];
const axis = { fontSize: 11, fill: "var(--color-ink-faint)" };
const tooltip = {
  background: "var(--color-day)",
  color: "var(--color-ink)",
  border: "1px solid var(--color-line)",
  borderRadius: 8,
  fontSize: 12,
};

export function WeatherReferee({ siteId }: { siteId: string }) {
  const referee = useQuery({
    queryKey: ["referee", siteId],
    queryFn: () => api.referee(siteId),
    staleTime: Infinity,
    retry: false,
  });
  if (referee.isError) return <Failure error={referee.error} />;
  if (!referee.data) return <Loading label="checking the weather file against observations…" />;
  const data = referee.data;
  const nights = (data.night_minimum ?? []).map((item) => ({
    name: MONTHS[item.month - 1],
    file: item.file,
    observed: item.reference,
  }));
  const sun = (data.solar ?? []).map((item) => ({
    name: MONTHS[item.month - 1],
    file: item.file,
    satellite: item.reference,
  }));
  const flags = data.flags ?? [];
  return (
    <Panel
      title="Is the weather file telling the truth?"
      subtitle={`${data.weather_file} checked against ${data.station ? `IMD's own record for ${data.station}, ${num(data.station_distance_km, 1)} km away, and ` : ""}${data.solar_source ?? "a satellite solar reference"}.`}
    >
      {flags.length > 0 ? (
        <ul className="mb-4 space-y-2">
          {flags.map((flag) => (
            <li
              key={flag}
              className="rounded-md border border-[var(--color-diesel)]/50 bg-[var(--color-diesel)]/10 px-3 py-2 text-sm text-[var(--color-danger-ink)]"
            >
              {flag}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-4 rounded-md border border-[var(--color-good)]/40 bg-[var(--color-good)]/10 px-3 py-2 text-sm text-[var(--color-good)]">
          Nothing in this file disagrees with the references by more than the tolerances.
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {nights.length > 0 && (
          <div>
            <p className="mb-1 text-[13px] font-medium">Mean night low, °C</p>
            <div className="h-56">
              <ResponsiveContainer>
                <ComposedChart data={nights} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
                  <CartesianGrid stroke="var(--color-line-soft)" />
                  <XAxis dataKey="name" tick={axis} stroke="var(--color-line)" />
                  <YAxis tick={axis} stroke="var(--color-line)" />
                  <Tooltip contentStyle={tooltip} formatter={(value: number) => `${num(value, 1)} °C`} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line dataKey="observed" name="IMD observed" stroke="var(--color-good)" strokeWidth={2} dot={false} />
                  <Line dataKey="file" name="weather file" stroke="var(--color-diesel)" strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
        {sun.length > 0 && (
          <div>
            <p className="mb-1 text-[13px] font-medium">Sun on the ground, kWh/m² a day</p>
            <div className="h-56">
              <ResponsiveContainer>
                <ComposedChart data={sun} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
                  <CartesianGrid stroke="var(--color-line-soft)" />
                  <XAxis dataKey="name" tick={axis} stroke="var(--color-line)" />
                  <YAxis tick={axis} stroke="var(--color-line)" />
                  <Tooltip contentStyle={tooltip} formatter={(value: number) => `${num(value, 2)} kWh/m²`} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="satellite" name="NASA POWER (CERES)" fill="var(--color-cold)" />
                  <Bar dataKey="file" name="weather file" fill="var(--color-solar)" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Stat
          label="Coldest hour in the file"
          value={num(data.coldest_hour_c, 1)}
          unit="°C"
          tone={data.hours_below_record_low > 0 ? "bad" : "neutral"}
          note={
            data.record_low_c != null
              ? `IMD's all time record is ${num(data.record_low_c, 1)} °C (${data.record_low_date})`
              : undefined
          }
        />
        <Stat
          label="Hours below the record"
          value={num(data.hours_below_record_low, 0)}
          unit="h"
          tone={data.hours_below_record_low > 0 ? "bad" : "good"}
        />
        <Stat
          label="Sun in a year"
          value={num(data.annual_solar_file_kwh_m2, 0)}
          unit="kWh/m² in the file"
          note={
            data.annual_solar_reference_kwh_m2 != null
              ? `${num(data.annual_solar_reference_kwh_m2, 0)} kWh/m² by satellite`
              : undefined
          }
        />
      </div>
      {(data.notes ?? []).map((note) => (
        <p key={note} className="mt-2 text-[12px] text-[var(--color-ink-faint)]">
          {note}
        </p>
      ))}
    </Panel>
  );
}

export function DesignNight({ siteId }: { siteId: string }) {
  const night = useQuery({
    queryKey: ["design-night", siteId],
    queryFn: () => api.designNight(siteId),
    staleTime: Infinity,
    retry: false,
  });
  if (night.isError) return <Failure error={night.error} />;
  if (!night.data) return <Loading label="finding the 1 in 10 design night…" />;
  const data = night.data;
  const winters = data.winters.map((item) => ({
    name: `${item.winter - 1}-${String(item.winter).slice(2)}`,
    model: item.minimum_c,
    observed: item.observed_c ?? undefined,
  }));
  return (
    <Panel
      title="The night the shelter is designed for"
      subtitle={`Each winter's coldest night from ${data.source}. A Gumbel fit gives the 1 in ${num(data.return_period_years, 0)} year level, and the design night is the real night closest to it.`}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat
          label={`1 in ${num(data.return_period_years, 0)} year night`}
          value={num(data.return_level_c, 1)}
          unit="°C"
          tone="solar"
          large
        />
        <Stat
          label="Design night"
          value={data.night_of}
          note={`${num(data.minimum_c, 1)} °C at its coldest`}
        />
        <Stat
          label="Winters behind the fit"
          value={num(data.winters.length, 0)}
          note={data.station ? `checked against IMD at ${data.station}` : "no station to check against"}
        />
      </div>
      <div className="mt-4 h-56">
        <ResponsiveContainer>
          <ComposedChart data={winters} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--color-line-soft)" />
            <XAxis dataKey="name" tick={axis} stroke="var(--color-line)" />
            <YAxis tick={axis} stroke="var(--color-line)" />
            <Tooltip contentStyle={tooltip} formatter={(value: number) => `${num(value, 1)} °C`} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="model" name="coldest night, model" fill="var(--color-cold)" />
            <Line dataKey="observed" name="coldest night, IMD" stroke="var(--color-good)" strokeWidth={2} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-ink-faint)]">{data.source_note}</p>
      {(data.notes ?? []).map((note) => (
        <p key={note} className="mt-1 text-[12px] text-[var(--color-ink-faint)]">
          {note}
        </p>
      ))}
    </Panel>
  );
}
