import { useMemo, useState } from "react";
import type { GeometryPayload, SurfaceResults } from "../lib/api";
import { COLD_TO_HOT, SOLAR_SCALE, lerpColour, num } from "../lib/format";

type Vec = { x: number; y: number; z: number };

type Poly = {
  name: string;
  zone: string;
  kind: string;
  facade: string | null;
  points: Vec[];
  colour: string;
  area: number;
};

const ZONE_TINT: Record<string, string> = {
  ROOM: "#4e9bc4",
  SUNSPACE: "#f5a623",
  TROMBE_GAP: "#c4453d",
  VESTIBULE: "#8c8378",
};

function toPolys(geometry: GeometryPayload): Poly[] {
  return geometry.surfaces.map((surface) => {
    const points: Vec[] = [];
    for (let i = 0; i < surface.positions.length; i += 3) {
      points.push({
        x: surface.positions[i],
        y: surface.positions[i + 1],
        z: surface.positions[i + 2],
      });
    }
    return {
      name: surface.name,
      zone: surface.zone,
      kind: surface.kind,
      facade: surface.facade ?? null,
      points,
      colour: surface.colour,
      area: surface.area_m2,
    };
  });
}

function bounds(polys: Poly[], axis: "x" | "y" | "z") {
  let min = Infinity;
  let max = -Infinity;
  for (const poly of polys)
    for (const point of poly.points) {
      const value = point[axis];
      if (value < min) min = value;
      if (value > max) max = value;
    }
  return { min, max };
}

function valueAt(
  surfaces: SurfaceResults | null,
  name: string,
  field: "inside_face_temperature_c" | "incident_solar_w_m2",
  index: number,
): number | null {
  if (!surfaces) return null;
  const series = surfaces.surfaces.find((item) => item.surface === name);
  const values = series?.[field];
  if (!values || values.length === 0) return null;
  const value = values[Math.min(index, values.length - 1)];
  return value === null || value === undefined || !Number.isFinite(value) ? null : value;
}

function extentOf(
  surfaces: SurfaceResults | null,
  field: "inside_face_temperature_c" | "incident_solar_w_m2",
) {
  if (!surfaces) return null;
  let min = Infinity;
  let max = -Infinity;
  for (const series of surfaces.surfaces)
    for (const value of series[field] ?? []) {
      if (value === null || value === undefined || !Number.isFinite(value)) continue;
      if (value < min) min = value;
      if (value > max) max = value;
    }
  if (!Number.isFinite(min)) return null;
  return max - min < 1e-6 ? { min: min - 0.5, max: max + 0.5 } : { min, max };
}

export type DrawMode = "construction" | "surface_temperature" | "incident_solar" | "interior_temperature";

export function PlanView({
  geometry,
  surfaces,
  hourIndex,
  mode,
}: {
  geometry: GeometryPayload;
  surfaces: SurfaceResults | null;
  hourIndex: number;
  mode: DrawMode;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const polys = useMemo(() => toPolys(geometry), [geometry]);
  const field =
    mode === "incident_solar" ? "incident_solar_w_m2" : "inside_face_temperature_c";
  const extent = useMemo(
    () => (mode === "construction" ? null : extentOf(surfaces, field)),
    [surfaces, field, mode],
  );

  const x = bounds(polys, "x");
  const y = bounds(polys, "y");
  const width = x.max - x.min;
  const height = y.max - y.min;
  const pad = Math.max(width, height) * 0.16;
  const vb = `${x.min - pad} ${-(y.max + pad)} ${width + pad * 2} ${height + pad * 2}`;

  const walls = polys.filter(
    (poly) => poly.kind === "wall" || poly.kind === "partition",
  );
  const windows = polys.filter((poly) => poly.kind === "window");
  const floors = polys.filter((poly) => poly.kind === "floor");

  const paint = (name: string, fallback: string) => {
    if (mode === "construction" || !extent) return fallback;
    const value = valueAt(surfaces, name, field, hourIndex);
    if (value === null) return "#4a5a70";
    return lerpColour(
      (value - extent.min) / (extent.max - extent.min),
      mode === "incident_solar" ? SOLAR_SCALE : COLD_TO_HOT,
    );
  };

  const stroke = Math.max(width, height) * 0.006;
  const hoverPoly = polys.find((poly) => poly.name === hover);
  const hoverValue = hover ? valueAt(surfaces, hover, field, hourIndex) : null;

  return (
    <div className="space-y-2">
      <div className="relative overflow-hidden rounded-lg border border-[var(--color-line)] bg-[var(--color-night)]">
        <svg viewBox={vb} className="h-[420px] w-full" role="img">
          <defs>
            <pattern
              id="plan-grid"
              width="1"
              height="1"
              patternUnits="userSpaceOnUse"
            >
              <path
                d="M 1 0 L 0 0 0 1"
                fill="none"
                stroke="var(--color-line)"
                strokeWidth={stroke * 0.25}
              />
            </pattern>
          </defs>
          <rect
            x={x.min - pad}
            y={-(y.max + pad)}
            width={width + pad * 2}
            height={height + pad * 2}
            fill="url(#plan-grid)"
          />

          {floors.map((poly) => (
            <polygon
              key={poly.name}
              points={poly.points.map((p) => `${p.x},${-p.y}`).join(" ")}
              fill={ZONE_TINT[poly.zone] ?? "#4e9bc4"}
              fillOpacity={0.08}
              stroke={ZONE_TINT[poly.zone] ?? "#4e9bc4"}
              strokeOpacity={0.35}
              strokeWidth={stroke * 0.5}
              strokeDasharray={`${stroke * 3} ${stroke * 2}`}
            />
          ))}

          {walls.map((poly) => {
            const xs = poly.points.map((p) => p.x);
            const ys = poly.points.map((p) => p.y);
            const x0 = Math.min(...xs);
            const y0 = Math.min(...ys);
            const w = Math.max(...xs) - x0;
            const h = Math.max(...ys) - y0;
            const thin = Math.max(stroke * 2.2, Math.min(width, height) * 0.02);
            return (
              <rect
                key={poly.name}
                x={x0 - (w < 1e-6 ? thin / 2 : 0)}
                y={-(y0 + h) - (h < 1e-6 ? thin / 2 : 0)}
                width={w < 1e-6 ? thin : w}
                height={h < 1e-6 ? thin : h}
                fill={paint(poly.name, poly.colour)}
                stroke="var(--color-ink)"
                strokeWidth={stroke * 0.4}
                opacity={hover && hover !== poly.name ? 0.55 : 1}
                onMouseEnter={() => setHover(poly.name)}
                onMouseLeave={() => setHover(null)}
              />
            );
          })}

          {windows.map((poly) => {
            const xs = poly.points.map((p) => p.x);
            const ys = poly.points.map((p) => p.y);
            const x0 = Math.min(...xs);
            const y0 = Math.min(...ys);
            const w = Math.max(...xs) - x0;
            const h = Math.max(...ys) - y0;
            const thin = Math.max(stroke * 2.8, Math.min(width, height) * 0.026);
            return (
              <rect
                key={poly.name}
                x={x0 - (w < 1e-6 ? thin / 2 : 0)}
                y={-(y0 + h) - (h < 1e-6 ? thin / 2 : 0)}
                width={w < 1e-6 ? thin : w}
                height={h < 1e-6 ? thin : h}
                fill={paint(poly.name, "#8fd0ef")}
                stroke="var(--color-night-2)"
                strokeWidth={stroke * 0.35}
                onMouseEnter={() => setHover(poly.name)}
                onMouseLeave={() => setHover(null)}
              />
            );
          })}

          <g stroke="var(--color-ink-faint)" strokeWidth={stroke * 0.4} fill="none">
            <line x1={x.min} y1={-(y.min - pad * 0.45)} x2={x.max} y2={-(y.min - pad * 0.45)} />
            <line
              x1={x.min - pad * 0.45}
              y1={-y.min}
              x2={x.min - pad * 0.45}
              y2={-y.max}
            />
          </g>
          <text
            x={(x.min + x.max) / 2}
            y={-(y.min - pad * 0.72)}
            fill="var(--color-ink-dim)"
            fontSize={pad * 0.3}
            textAnchor="middle"
          >
            {num(width, 2)} m
          </text>
          <text
            x={x.min - pad * 0.62}
            y={-(y.min + y.max) / 2}
            fill="var(--color-ink-dim)"
            fontSize={pad * 0.3}
            textAnchor="middle"
            transform={`rotate(-90 ${x.min - pad * 0.62} ${-(y.min + y.max) / 2})`}
          >
            {num(height, 2)} m
          </text>

          <g transform={`translate(${x.max + pad * 0.35} ${-(y.max + pad * 0.35)})`}>
            <line
              x1={0}
              y1={0}
              x2={0}
              y2={-pad * 0.55}
              stroke="#c4453d"
              strokeWidth={stroke * 0.6}
            />
            <text
              x={0}
              y={-pad * 0.68}
              fill="#c4453d"
              fontSize={pad * 0.32}
              textAnchor="middle"
            >
              N
            </text>
          </g>
        </svg>

        {hoverPoly && (
          <div className="pointer-events-none absolute left-3 top-3 rounded-md border border-[var(--color-line)] bg-[var(--color-night)]/95 px-2.5 py-1.5 text-[11px]">
            <div className="font-mono">{hoverPoly.name}</div>
            <div className="text-[var(--color-ink-faint)]">
              {hoverPoly.zone} · {num(hoverPoly.area, 2)} m²
              {hoverValue !== null
                ? ` · ${num(hoverValue)} ${
                    mode === "incident_solar" ? "W/m²" : "°C"
                  }`
                : ""}
            </div>
          </div>
        )}
      </div>

      <p className="text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
        A true plan projection of the surfaces that were simulated, looking straight down
        with north up. Wall thickness here is the drawn line weight, not the construction
        thickness; the build-up is on the construction tab.
      </p>
    </div>
  );
}

export function SectionView({
  geometry,
  surfaces,
  hourIndex,
  mode,
  axis,
}: {
  geometry: GeometryPayload;
  surfaces: SurfaceResults | null;
  hourIndex: number;
  mode: DrawMode;
  axis: "north-south" | "east-west";
}) {
  const polys = useMemo(() => toPolys(geometry), [geometry]);
  const field =
    mode === "incident_solar" ? "incident_solar_w_m2" : "inside_face_temperature_c";
  const extent = useMemo(
    () => (mode === "construction" ? null : extentOf(surfaces, field)),
    [surfaces, field, mode],
  );

  const horizontal = axis === "north-south" ? "y" : "x";
  const h = bounds(polys, horizontal);
  const z = bounds(polys, "z");
  const width = h.max - h.min;
  const height = Math.max(z.max - z.min, 1);
  const pad = Math.max(width, height) * 0.18;
  const vb = `${h.min - pad} ${-(z.max + pad)} ${width + pad * 2} ${height + pad * 2}`;

  const paint = (name: string, fallback: string) => {
    if (mode === "construction" || !extent) return fallback;
    const value = valueAt(surfaces, name, field, hourIndex);
    if (value === null) return "#4a5a70";
    return lerpColour(
      (value - extent.min) / (extent.max - extent.min),
      mode === "incident_solar" ? SOLAR_SCALE : COLD_TO_HOT,
    );
  };

  const stroke = Math.max(width, height) * 0.008;

  return (
    <div className="space-y-2">
      <div className="overflow-hidden rounded-lg border border-[var(--color-line)] bg-[var(--color-night)]">
        <svg viewBox={vb} className="h-[360px] w-full" role="img">
          <line
            x1={h.min - pad}
            y1={0}
            x2={h.max + pad}
            y2={0}
            stroke="#8c8378"
            strokeWidth={stroke * 0.8}
          />
          {polys
            .filter((poly) => {
              const parallel =
                axis === "north-south"
                  ? ["east", "west"]
                  : ["north", "south"];
              if (poly.facade && parallel.includes(poly.facade)) return false;
              return true;
            })
            .map((poly) => {
              const hs = poly.points.map((p) => p[horizontal]);
              const zs = poly.points.map((p) => p.z);
              const h0 = Math.min(...hs);
              const z0 = Math.min(...zs);
              const w = Math.max(...hs) - h0;
              const t = Math.max(...zs) - z0;
              const thin = Math.max(stroke * 2.5, height * 0.055);
              const isWindow = poly.kind === "window";
              const isSlab = poly.kind === "floor" || poly.kind === "roof";
              return (
                <rect
                  key={poly.name}
                  x={h0 - (w < 1e-6 ? thin / 2 : 0)}
                  y={-(z0 + t) - (t < 1e-6 ? thin / 2 : 0)}
                  width={w < 1e-6 ? thin : w}
                  height={t < 1e-6 ? thin : t}
                  fill={paint(poly.name, isWindow ? "#8fd0ef" : poly.colour)}
                  fillOpacity={isWindow ? 0.9 : isSlab ? 1 : 0.95}
                  stroke="var(--color-ink)"
                  strokeWidth={stroke * 0.35}
                >
                  <title>{poly.name}</title>
                </rect>
              );
            })}
          <text
            x={h.min}
            y={-(z.max + pad * 0.4)}
            fill="var(--color-ink-dim)"
            fontSize={pad * 0.32}
          >
            {axis === "north-south" ? "south ←→ north" : "west ←→ east"} · ceiling{" "}
            {num(z.max, 2)} m
          </text>
        </svg>
      </div>
      <p className="text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
        Sliced through the same surfaces the simulator used, so the glazing height and sill
        you see are the ones the solar gain was computed against.
      </p>
    </div>
  );
}
