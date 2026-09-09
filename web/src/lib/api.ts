import type { components } from "./schema";

type S = components["schemas"];

export type DesignSpec = S["DesignSpec"];
export type SimulationResult = S["SimulationResult"];
export type GeometryPayload = S["GeometryPayload"];
export type ComparisonTable = S["ComparisonTable"];
export type MeshSurface = S["MeshSurface"];
export type LayoutPayload = S["LayoutPayload"];
export type RoomPayload = S["RoomPayload"];
export type PartitionPayload = S["PartitionPayload"];
export type ShelterUseSummary = S["ShelterUseSummary"];
export type DesignSummary = S["DesignSummary"];
export type ComfortModel = S["ComfortModel"];
export type SurfaceSeries = S["SurfaceSeries"];
export type SurfaceResults = S["SurfaceResults"];
export type AirflowSummary = S["AirflowSummary"];
export type ZoneAirflow = S["ZoneAirflow"];
export type ZoneCoupling = S["ZoneCoupling"];
export type HourlySeries = S["HourlySeries"];
export type EngineWarning = S["EngineWarning"];
export type Assumption = S["Assumption"];
export type Recommendation = S["Recommendation"];
export type UncertaintyBand = S["UncertaintyBand"];
export type MetaResponse = S["MetaResponse"];
export type SiteSummary = S["SiteSummary"];
export type Material = S["Material"];
export type ValidationEvidence = S["ValidationEvidence"];
export type CFDExport = S["CFDExport"];
export type CFDAbsent = S["CFDAbsent"];
export type Window = S["Window"];
export type SunPath = S["SunPath"];
export type SunPosition = S["SunPosition"];
export type TerrainMesh = S["TerrainMesh"];
export type ClimateSummary = S["ClimateSummary"];
export type ClimateMonth = S["ClimateMonth"];
export type ParetoResult = S["ParetoResult"];
export type ParetoPoint = S["ParetoPoint"];
export type OptimisationJob = S["OptimisationJob"];
export type DesignVariable = S["DesignVariable"];

export class ApiError extends Error {
  constructor(readonly status: number, message: string, readonly body?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  let body: unknown = undefined;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `${response.status} ${response.statusText}`;
    throw new ApiError(response.status, detail, body);
  }
  return body as T;
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, payload: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(payload) });

export const api = {
  meta: () => get<MetaResponse>("/api/meta"),
  materials: () => get<Material[]>("/api/materials"),
  shelterUses: () => get<ShelterUseSummary[]>("/api/shelter-uses"),
  designs: () => get<DesignSummary[]>("/api/designs"),
  comfortModels: () => get<ComfortModel[]>("/api/comfort-models"),
  design: (identifier: string) => get<DesignSpec>(`/api/designs/${identifier}`),
  references: () => get<DesignSpec[]>("/api/references"),
  reference: (name: string) => get<DesignSpec>(`/api/references/${name}`),
  validation: () => get<ValidationEvidence>("/api/validation"),
  geometry: (design: DesignSpec, siteId?: string | null) =>
    post<GeometryPayload>("/api/geometry", { design, site_id: siteId ?? null }),
  simulate: (payload: {
    design: DesignSpec;
    site_id?: string | null;
    period: Window;
    surface_period?: Window | null;
    conditioned_run?: boolean;
    surface_output?: boolean;
    with_uncertainty?: boolean;
    with_reference?: boolean;
    comfort_model_id?: string | null;
  }) => post<SimulationResult>("/api/simulate", payload),
  compare: (payload: {
    designs: DesignSpec[];
    site_id?: string | null;
    period: Window;
    conditioned_run?: boolean;
    comfort_model_id?: string | null;
  }) => post<ComparisonTable>("/api/compare", payload),
  cfd: (designHash: string) =>
    get<CFDExport | CFDAbsent>(`/api/cfd/${designHash}`),
  sunPath: (siteId: string | null | undefined, day: string) =>
    get<SunPath>(
      `/api/sun-path?day=${day}${siteId ? `&site_id=${siteId}` : ""}&step_minutes=15`,
    ),
  climate: (siteId: string | null | undefined, windowDays = 7) =>
    get<ClimateSummary>(
      `/api/climate?window_days=${windowDays}` + (siteId ? `&site_id=${siteId}` : ""),
    ),
  runs: () => get<Record<string, unknown>[]>("/api/runs?limit=200"),
  optimise: (payload: {
    base_design: DesignSpec;
    variables: DesignVariable[];
    objectives?: string[];
    constraints?: Record<string, unknown>;
    population_size?: number;
    generations?: number;
    seed?: number;
    site_id?: string | null;
    period: Window;
  }) => post<OptimisationJob>("/api/optimise", payload),
  optimisationStatus: (jobId: string) =>
    get<OptimisationJob>(`/api/optimise/${jobId}`),
  terrain: (siteId: string | null | undefined, inner = 110, outer = 320) =>
    get<TerrainMesh>(
      `/api/terrain?inner_radius_m=${inner}&outer_radius_m=${outer}` +
        (siteId ? `&site_id=${siteId}` : ""),
    ),
};

export function isCfdAbsent(value: CFDExport | CFDAbsent): value is CFDAbsent {
  return (value as CFDAbsent).available === false;
}

export function seriesByName(
  result: SimulationResult,
  name: string,
): HourlySeries | undefined {
  return result.hourly?.find((item) => item.name === name);
}
