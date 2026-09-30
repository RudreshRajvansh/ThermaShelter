import { useQuery } from "@tanstack/react-query";
import { api, type SitingMap as SitingMapData } from "../lib/api";
import { Failure, Loading, Panel, Stat } from "./Kit";
import { num } from "../lib/format";

const SIZE = 420;

function Grid({ data }: { data: SitingMapData }) {
  const cells = data.cells;
  const hours = cells.map((cell) => cell.winter_sun_h);
  const low = Math.min(...hours);
  const high = Math.max(...hours);
  const span = Math.max(high - low, 1e-6);
  const side = SIZE / data.columns;
  const best = new Map(data.best.map((index, rank) => [index, rank + 1]));
  const half = data.half_width_m;
  const place = (east: number, north: number) => ({
    x: ((east + half) / (2 * half)) * (SIZE - side),
    y: ((half - north) / (2 * half)) * (SIZE - side),
  });
  return (
    <svg
      viewBox={`0 0 ${SIZE + 60} ${SIZE + 30}`}
      className="w-full max-w-[520px]"
      role="img"
      aria-label="Winter sun hours on a grid around the site, north up"
    >
      {cells.map((cell, index) => {
        const { x, y } = place(cell.east_m, cell.north_m);
        const share = (cell.winter_sun_h - low) / span;
        return (
          <rect
            key={index}
            x={x}
            y={y}
            width={side}
            height={side}
            fill="var(--color-solar)"
            fillOpacity={0.12 + 0.88 * share}
            stroke="var(--color-day)"
            strokeWidth={0.5}
          >
            <title>
              {`${num(cell.east_m, 0)} m east, ${num(cell.north_m, 0)} m north: ${num(cell.winter_sun_h, 2)} h of winter sun, ${num(cell.lost_to_terrain_h, 2)} h lost to the terrain, slope ${num(cell.slope_deg, 1)}°`}
            </title>
          </rect>
        );
      })}
      {(() => {
        const { x, y } = place(cells[data.site_cell].east_m, cells[data.site_cell].north_m);
        return (
          <rect x={x + 1} y={y + 1} width={side - 2} height={side - 2} fill="none"
            stroke="var(--color-ink)" strokeWidth={2} strokeDasharray="4 3" />
        );
      })()}
      {[...best.entries()].map(([index, rank]) => {
        const { x, y } = place(cells[index].east_m, cells[index].north_m);
        return (
          <g key={index}>
            <circle cx={x + side / 2} cy={y + side / 2} r={Math.min(11, side / 2.4)}
              fill="var(--color-day)" stroke="var(--color-ink)" strokeWidth={1.5} />
            <text x={x + side / 2} y={y + side / 2 + 4} textAnchor="middle" fontSize={11}
              fill="var(--color-ink)">{rank}</text>
          </g>
        );
      })}
      <g transform={`translate(${SIZE + 30},30)`}>
        <polygon points="0,-18 6,6 0,1 -6,6" fill="var(--color-ink)" />
        <text y={-24} textAnchor="middle" fontSize={11} fill="var(--color-ink)">N</text>
      </g>
      <text x={0} y={SIZE + 22} fontSize={11} fill="var(--color-ink-faint)">
        {`${num(2 * half / 1000, 1)} km across, cells ${num(data.step_m, 0)} m; faint ${num(low, 1)} h to strong ${num(high, 1)} h, the dashed cell is the site`}
      </text>
    </svg>
  );
}

export function SitingMap({ siteId }: { siteId: string }) {
  const map = useQuery({
    queryKey: ["siting-map", siteId],
    queryFn: () => api.sitingMap(siteId),
    staleTime: Infinity,
    retry: false,
  });
  if (map.isError) return <Failure error={map.error} />;
  if (!map.data) return <Loading label="tracing the winter sun over the ground around the site…" />;
  const data = map.data;
  const site = data.cells[data.site_cell];
  const top = data.cells[data.best[0]];
  return (
    <Panel
      title="Where on the ground to build"
      subtitle={`Hours of direct winter sun a day, ${data.winter_from} to ${data.winter_to}, with each point's own mountain horizon.`}
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        <Grid data={data} />
        <div className="space-y-3">
          <Stat
            label="At the chosen point"
            value={num(site.winter_sun_h, 1)}
            unit="h a day"
            note={`${num(site.lost_to_terrain_h, 1)} h lost to the mountains against a flat horizon's ${num(data.flat_winter_sun_h, 1)} h`}
          />
          {top && (
            <Stat
              label="Best spot nearby"
              value={num(top.winter_sun_h, 1)}
              unit="h a day"
              tone={top.winter_sun_h - site.winter_sun_h >= 0.25 ? "solar" : "neutral"}
              note={`${num(top.east_m, 0)} m east and ${num(top.north_m, 0)} m north, slope ${num(top.slope_deg, 1)}°${top.winter_sun_h - site.winter_sun_h < 0.25 ? "; the chosen point is within a quarter hour of it" : ""}`}
            />
          )}
          <p className="text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
            {data.source.citation}. {data.source.note}
          </p>
        </div>
      </div>
    </Panel>
  );
}
