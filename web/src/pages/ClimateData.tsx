import { useQuery } from "@tanstack/react-query";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import { Failure, Loading, Panel, Stat, Warnings } from "../components/Kit";
import { useStore } from "../lib/store";
import { num } from "../lib/format";

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

const axis = { fontSize: 11, fill: "var(--color-ink-faint)" };
const grid = "#22334c";

export function ClimateData() {
  const store = useStore();
  const climate = useQuery({
    queryKey: ["climate", store.siteId],
    queryFn: () => api.climate(store.siteId),
    enabled: Boolean(store.siteId),
    staleTime: Infinity,
  });

  if (climate.isError) return <Failure error={climate.error} />;
  if (!climate.data) return <Loading label="reading the weather file…" />;
  const data = climate.data;

  const monthly = data.months.map((month) => ({
    name: MONTHS[month.month - 1],
    mean: month.mean_dry_bulb_c,
    min: month.min_dry_bulb_c,
    max: month.max_dry_bulb_c,
    swing: month.mean_daily_swing_k,
    span: [month.min_dry_bulb_c, month.max_dry_bulb_c] as [number, number],
    solar: month.global_horizontal_kwh_m2,
    direct: month.direct_normal_kwh_m2,
    wind: month.mean_wind_m_s,
    ground: month.ground_temperature_c,
    hdd: month.heating_degree_days,
    cdd: month.cooling_degree_days,
  }));

  const window = data.design_day_window.timestamps.map((stamp, index) => ({
    t: stamp,
    label: `${stamp.slice(5, 10)} ${stamp.slice(11, 13)}h`,
    dry: data.design_day_window.dry_bulb_c[index],
    global: data.design_day_window.global_horizontal_w_m2[index],
    direct: data.design_day_window.direct_normal_w_m2[index],
    wind: data.design_day_window.wind_speed_m_s[index],
  }));

  const horizon = data.horizon_azimuths_deg.map((azimuth, index) => ({
    azimuth: `${azimuth}°`,
    blocked: data.horizon_blocked_deg[index],
  }));

  const pressureGap =
    Math.abs(data.mean_station_pressure_pa - data.expected_station_pressure_pa) /
    data.expected_station_pressure_pa;

  return (
    <div className="space-y-4">
      <Panel
        title={`${data.name} — the weather year everything is computed against`}
        subtitle={`${data.hours} hourly rows from ${data.weather_file}. Station "${data.weather_station}", source "${data.weather_source}". Nothing on this page is modelled; it is read straight out of the file.`}
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Annual mean" value={num(data.annual_mean_c)} unit="°C" />
          <Stat
            label="Annual range"
            value={`${num(data.annual_min_c)} to ${num(data.annual_max_c)}`}
            unit="°C"
          />
          <Stat
            label="Heating degree days"
            value={num(data.heating_degree_days, 0)}
            unit="base 18 °C"
            tone={data.heating_degree_days > data.cooling_degree_days ? "solar" : "neutral"}
          />
          <Stat
            label="Cooling degree days"
            value={num(data.cooling_degree_days, 0)}
            unit="base 18 °C"
            tone={data.cooling_degree_days > data.heating_degree_days ? "solar" : "neutral"}
          />
          <Stat
            label="Global horizontal solar"
            value={num(data.annual_global_horizontal_kwh_m2, 0)}
            unit="kWh/m² per year"
            tone="solar"
          />
          <Stat
            label="Direct normal solar"
            value={num(data.annual_direct_normal_kwh_m2, 0)}
            unit="kWh/m² per year"
          />
          <Stat label="Mean wind" value={num(data.mean_wind_m_s)} unit="m/s" />
          <Stat
            label="Design day"
            value={data.design_day}
            note={`${num(data.design_day_min_c)} to ${num(data.design_day_max_c)} °C`}
          />
        </div>
      </Panel>

      <Panel
        title="Air pressure at altitude"
        subtitle="The single most dangerous column in the file. Heat carried by air depends on its mass, and EnergyPlus will not warn if this is wrong."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Station pressure in the file"
            value={num(data.mean_station_pressure_pa, 0)}
            unit="Pa"
          />
          <Stat
            label="Expected at this elevation"
            value={num(data.expected_station_pressure_pa, 0)}
            unit="Pa"
            note={`barometric relation at ${num(data.elevation_m, 0)} m`}
          />
          <Stat
            label="Air density used"
            value={num(data.air_density_kg_m3, 3)}
            unit="kg/m³"
            tone={pressureGap > 0.05 ? "bad" : "good"}
          />
          <Stat
            label="At sea level it would be"
            value={num(data.sea_level_air_density_kg_m3, 3)}
            unit="kg/m³"
            note={`${num(
              (1 - data.air_density_kg_m3 / data.sea_level_air_density_kg_m3) * 100,
              0,
            )} percent lighter here`}
          />
        </div>
        {pressureGap > 0.05 && (
          <p className="mt-3 rounded-md border border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 p-3 text-xs leading-relaxed text-[#ffb4ae]">
            The pressure column disagrees with the site elevation by{" "}
            {num(pressureGap * 100, 1)} percent. Every air related heat transfer in the run
            is scaled by this. Fix the file in Layer 0 before trusting any result.
          </p>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Temperature through the year"
          subtitle="Monthly mean with the full range behind it, and the mean daily swing that thermal mass works against."
        >
          <div className="h-[260px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={monthly} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
                <CartesianGrid stroke={grid} strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={axis} stroke={grid} />
                <YAxis unit="°" tick={axis} stroke={grid} width={46} />
                <Tooltip
                  contentStyle={{
                    background: "#0b1220",
                    border: "1px solid #22334c",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Area
                  dataKey="span"
                  name="monthly range"
                  stroke="none"
                  fill="var(--color-cold)"
                  fillOpacity={0.16}
                  isAnimationActive={false}
                />
                <Line
                  dataKey="mean"
                  name="mean air"
                  stroke="var(--color-day)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  dataKey="ground"
                  name="ground at 0.5 m"
                  stroke="var(--color-stone)"
                  strokeWidth={1.5}
                  strokeDasharray="4 3"
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
                <Line
                  dataKey="swing"
                  name="mean daily swing (K)"
                  stroke="var(--color-solar)"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel
          title="Solar resource by month"
          subtitle="Global horizontal is what lands on a flat roof; direct normal is what a south wall or a collector sees."
        >
          <div className="h-[260px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={monthly} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
                <CartesianGrid stroke={grid} strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={axis} stroke={grid} />
                <YAxis tick={axis} stroke={grid} width={46} />
                <Tooltip
                  contentStyle={{
                    background: "#0b1220",
                    border: "1px solid #22334c",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: unknown, name: string) => [
                    `${num(value as number, 0)} kWh/m²`,
                    name,
                  ]}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar
                  dataKey="solar"
                  name="global horizontal"
                  fill="var(--color-solar)"
                  fillOpacity={0.85}
                  isAnimationActive={false}
                />
                <Line
                  dataKey="direct"
                  name="direct normal"
                  stroke="var(--color-day)"
                  strokeWidth={1.75}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          title={`Hour by hour around the design day (${data.design_day})`}
          subtitle="The week the worst night sits in. This is the weather the shelter has to survive."
          className="lg:col-span-2"
        >
          <div className="h-[240px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={window} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
                <CartesianGrid stroke={grid} strokeDasharray="3 3" />
                <XAxis
                  dataKey="label"
                  tick={axis}
                  stroke={grid}
                  interval={Math.max(1, Math.round(window.length / 7))}
                />
                <YAxis yAxisId="t" unit="°" tick={axis} stroke={grid} width={46} />
                <YAxis
                  yAxisId="s"
                  orientation="right"
                  tick={axis}
                  stroke={grid}
                  width={46}
                />
                <Tooltip
                  contentStyle={{
                    background: "#0b1220",
                    border: "1px solid #22334c",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Area
                  yAxisId="s"
                  dataKey="global"
                  name="global horizontal (W/m²)"
                  stroke="var(--color-solar)"
                  fill="var(--color-solar)"
                  fillOpacity={0.18}
                  strokeWidth={1}
                  isAnimationActive={false}
                />
                <Line
                  yAxisId="t"
                  dataKey="dry"
                  name="outdoor air (°C)"
                  stroke="var(--color-cold)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel
          title="Terrain horizon"
          subtitle={
            data.has_horizon
              ? "Blocked altitude by compass direction, sampled from elevation data. This is what shades the site."
              : "No horizon profile is attached, so a flat skyline is assumed in every direction."
          }
        >
          {data.has_horizon ? (
            <>
              <div className="h-[200px]">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={horizon} outerRadius="78%">
                    <PolarGrid stroke={grid} />
                    <PolarAngleAxis
                      dataKey="azimuth"
                      tick={{ fontSize: 9, fill: "var(--color-ink-faint)" }}
                    />
                    <PolarRadiusAxis
                      tick={{ fontSize: 9, fill: "var(--color-ink-faint)" }}
                      stroke={grid}
                    />
                    <Radar
                      dataKey="blocked"
                      name="blocked altitude °"
                      stroke="var(--color-stone)"
                      fill="var(--color-stone)"
                      fillOpacity={0.35}
                      isAnimationActive={false}
                    />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-center text-[11px]">
                <div>
                  <div className="text-[var(--color-ink-faint)]">astronomical</div>
                  <div className="tnum">{num(data.sun_path.daylight_hours, 1)} h</div>
                </div>
                <div>
                  <div className="text-[var(--color-ink-faint)]">clear of ridge</div>
                  <div className="tnum">
                    {num(data.sun_path.visible_daylight_hours, 1)} h
                  </div>
                </div>
                <div>
                  <div className="text-[var(--color-ink-faint)]">lost</div>
                  <div className="tnum font-semibold text-[var(--color-diesel)]">
                    {num(data.sun_path.daylight_lost_to_terrain_h, 1)} h
                  </div>
                </div>
              </div>
            </>
          ) : (
            <p className="text-xs leading-relaxed text-[var(--color-ink-dim)]">
              Without a horizon table the simulation assumes the sun appears at
              astronomical sunrise. In a valley that overstates the daily solar gain.
            </p>
          )}
        </Panel>
      </div>

      {(data.warnings.length > 0 || data.assumptions.length > 0) && (
        <Panel
          title="What the weather file said about itself"
          subtitle="Read on load, before any simulation ran."
        >
          <Warnings warnings={data.warnings} />
          {data.assumptions.length > 0 && (
            <ul className="mt-3 space-y-1 text-[11px] text-[var(--color-ink-faint)]">
              {data.assumptions.map((item, index) => (
                <li key={index}>
                  <span className="font-mono">{item.name}</span> = {String(item.value)}{" "}
                  {item.unit ?? ""} — {item.source.citation}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => store.open("construction")}
          className="rounded-md bg-[var(--color-solar)] px-4 py-2 text-sm font-medium text-[#0b1220]"
        >
          Set design parameters →
        </button>
      </div>
    </div>
  );
}
