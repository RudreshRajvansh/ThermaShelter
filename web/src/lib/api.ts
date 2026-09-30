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
export type LiveWeather = S["LiveWeather"];
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
export type GlazingSystem = S["GlazingSystem"];
export type PlaceResult = S["PlaceResult"];
export type SiteDetail = S["SiteDetail"];
export type SiteProvenance = S["SiteProvenance"];
export type SiteSpec = S["SiteSpec"];
export type CFDExport = S["CFDExport"];
export type CFDAbsent = S["CFDAbsent"];
export type CFDField = S["CFDField"];
export type CFDFieldSet = S["CFDFieldSet"];
export type CFDBoundarySet = S["CFDBoundarySet"];
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
export type LeverPayload = S["LeverPayload"];
export type RobustnessRow = S["RobustnessRow"];
export type DesignNightPayload = S["DesignNightPayload"];
export type RefereePayload = S["RefereePayload"];
export type MonthCheckPayload = S["MonthCheckPayload"];
export type EndurancePayload = S["EndurancePayload"];
export type LeftoverHeatPayload = S["LeftoverHeatPayload"];
export type HorizonValuePayload = S["HorizonValuePayload"];
export type AirQualityMetrics = S["AirQualityMetrics"];
export type MapdlReport = S["MapdlReport"];
export type BillOfQuantities = S["BillOfQuantities"];
export type ComplianceSheet = S["ComplianceSheet"];
export type SitingMap = S["SitingMap"];
export type Post = S["Post"];
export type PostSummary = S["PostSummary"];
export type CommanderCard = S["CommanderCard"];
export type CardResult = S["CardResult"];
export type PostCalibration = S["PostCalibration"];
export type MapdlSet = S["MapdlSet"];
export type MapdlAbsent = S["MapdlAbsent"];
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly path?: string,
    readonly body?: unknown,
  ) {
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
    const unrouted = response.status === 404 && detail.trim() === "Not Found";
    throw new ApiError(
      response.status,
      unrouted
        ? `The server has no ${path.split("?")[0]} endpoint. The interface is talking to `
          + "an API that is older than it is, so restart the API and reload this page."
        : detail,
      path,
      body,
    );
  }
  return body as T;
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, payload: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(payload) });

export function evidenceUrl(runHash: string): string {
  return `${BASE}/api/evidence/${runHash}/zip`;
}

export async function designPack(payload: {
  design: DesignSpec;
  site_id?: string | null;
  period?: Window;
  run_hash?: string | null;
}): Promise<Blob> {
  const response = await fetch(`${BASE}/api/design-pack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = JSON.parse(text);
      if (body && typeof body === "object" && "detail" in body) detail = String(body.detail);
    } catch {
      if (text) detail = text;
    }
    throw new ApiError(response.status, detail, "/api/design-pack");
  }
  return response.blob();
}

export const api = {
  meta: () => get<MetaResponse>("/api/meta"),
  materials: () => get<Material[]>("/api/materials"),
  glazing: () => get<GlazingSystem[]>("/api/glazing"),
  places: (query: string) =>
    get<PlaceResult[]>(`/api/places?query=${encodeURIComponent(query)}&count=8`),
  resolveSite: (payload: {
    latitude_deg: number;
    longitude_deg: number;
    name?: string | null;
  }) => post<SiteDetail>("/api/sites/resolve", payload),
  site: (siteId: string) => get<SiteDetail>(`/api/sites/${encodeURIComponent(siteId)}`),
  shelterUses: () => get<ShelterUseSummary[]>("/api/shelter-uses"),
  designs: () => get<DesignSummary[]>("/api/designs"),
  comfortModels: () => get<ComfortModel[]>("/api/comfort-models"),
  liveWeather: (siteId: string | null) =>
    get<LiveWeather>(`/api/live-weather${siteId ? `?site_id=${siteId}` : ""}`),
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
    get<CFDFieldSet | CFDAbsent>(`/api/cfd/${designHash}`),
  billOfQuantities: (design: DesignSpec, siteId?: string | null) =>
    post<BillOfQuantities>("/api/bill-of-quantities", { design, site_id: siteId ?? null }),
  byeLawSheet: (design: DesignSpec, siteId?: string | null) =>
    post<ComplianceSheet>("/api/bye-law-sheet", { design, site_id: siteId ?? null }),
  mapdl: (designHash: string, siteId?: string | null) =>
    get<MapdlSet | MapdlAbsent>(
      `/api/mapdl/${designHash}${siteId ? `?site_id=${encodeURIComponent(siteId)}` : ""}`,
    ),
  cfdBoundaryConditions: (designHash: string) =>
    get<CFDBoundarySet>(`/api/cfd/${designHash}/boundary-conditions`),
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
    robust_top?: number;
    robust_keep?: number;
    site_id?: string | null;
    period: Window;
  }) => post<OptimisationJob>("/api/optimise", payload),
  searchLevers: (design: DesignSpec, siteId?: string | null) =>
    post<LeverPayload[]>("/api/search-levers", { design, site_id: siteId ?? null }),
  designNight: (siteId: string) =>
    get<DesignNightPayload>(`/api/sites/${encodeURIComponent(siteId)}/design-night`),
  posts: (lang: string) => get<PostSummary[]>(`/api/posts?lang=${lang}`),
  savePost: (post: Post) =>
    request<Post>(`/api/posts/${encodeURIComponent(post.id)}`, {
      method: "PUT",
      body: JSON.stringify(post),
    }),
  deletePost: (id: string) =>
    request<void>(`/api/posts/${encodeURIComponent(id)}`, { method: "DELETE" }),
  issueCard: (id: string, lang: string) =>
    post<CommanderCard>(`/api/posts/${encodeURIComponent(id)}/card?lang=${lang}`, {}),
  lastCard: (id: string, lang: string) =>
    get<CommanderCard>(`/api/posts/${encodeURIComponent(id)}/card?lang=${lang}`),
  issueAll: (lang: string) => post<CardResult[]>(`/api/posts/cards?lang=${lang}`, {}),
  calibratePost: (id: string, csv: string) =>
    request<PostCalibration>(`/api/posts/${encodeURIComponent(id)}/calibration`, {
      method: "POST",
      body: csv,
      headers: { "Content-Type": "text/csv" },
    }),
  sitingMap: (siteId: string) =>
    get<SitingMap>(`/api/sites/${encodeURIComponent(siteId)}/siting-map`),
  referee: (siteId: string) =>
    get<RefereePayload>(`/api/sites/${encodeURIComponent(siteId)}/referee`),
  endurance: (payload: { design: DesignSpec; site_id?: string | null; floor_c?: number }) =>
    post<EndurancePayload>("/api/endurance", payload),
  horizonValue: (payload: { design: DesignSpec; site_id?: string | null; period: Window }) =>
    post<HorizonValuePayload>("/api/horizon-value", payload),
  optimisationStatus: (jobId: string) =>
    get<OptimisationJob>(`/api/optimise/${jobId}`),
  terrain: (siteId: string | null | undefined, inner = 110, outer = 320) =>
    get<TerrainMesh>(
      `/api/terrain?inner_radius_m=${inner}&outer_radius_m=${outer}` +
        (siteId ? `&site_id=${siteId}` : ""),
    ),
};

export function isCfdAbsent(value: CFDFieldSet | CFDAbsent): value is CFDAbsent {
  return (value as CFDAbsent).available === false;
}

export function seriesByName(
  result: SimulationResult,
  name: string,
): HourlySeries | undefined {
  return result.hourly?.find((item) => item.name === name);
}
