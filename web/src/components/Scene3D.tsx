import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import {
  api,
  isCfdAbsent,
  type AirflowSummary,
  type CFDAbsent,
  type CFDField,
  type CFDFieldSet,
  type GeometryPayload,
  type SunPath,
  type SurfaceResults,
  type TerrainMesh,
} from "../lib/api";
import { buildSolid } from "../lib/solid";
import { applyClipping, buildArchitecture } from "../lib/architecture";
import { createThermalVolume, probeTemperature, type ThermalFieldData } from "../lib/thermal_volume";
import { activeDesign, useStore } from "../lib/store";
import {
  COLD_TO_HOT,
  SOLAR_SCALE,
  TURBO,
  lerpColour,
  rampCss,
  num,
} from "../lib/format";
import {
  createCompass,
  createGround,
  createSunDisc,
  createSunPathArc,
  createLayout,
  createTerrain,
  disposeObject,
  sunVector,
} from "../lib/scene";
import { createParticleAirflow } from "../lib/flow_paths";
import {
  createPipeline,
  createSkyDome,
  createSkyEnvironment,
  detailMaps,
  detectQuality,
  type Pipeline,
  type Quality,
  type SkyEnvironment,
} from "../lib/render_pipeline";
import {
  VIEW_HINT,
  VIEW_LABEL,
  createCameraRig,
  orbitFor,
  type CameraRig,
  type ViewName,
} from "../lib/viewer_camera";
import { createEdgeLines, type EdgeLines } from "../lib/edges";
import {
  cardinalPoints,
  createLabelLayer,
  dimensionLines,
  type LabelLayer,
} from "../lib/labels";
import { FIGURE_HEIGHT_M, createScaleFigure, type ScaleFigure } from "../lib/figure";
import { barLabel, paintHud, scaleBar } from "../lib/hud";
import type { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { createGizmos, computeNewDesign, type HandleType } from "../lib/manipulate";
import type { DesignSpec } from "../lib/api";

export type DisplayMode = "construction" | "surface_temperature" | "incident_solar" | "interior_temperature";

export type RampName = "diverging" | "spectral" | "solar";

export type Shell = "solid" | "wireframe" | "xray";

const SHELL_LABEL: Record<Shell, string> = {
  solid: "Solid",
  wireframe: "Wireframe",
  xray: "X-ray",
};

const RAMP_LABEL: Record<RampName, string> = {
  diverging: "Cold to hot",
  spectral: "Spectral",
  solar: "Night to sun",
};

function rampStops(mode: DisplayMode, ramp: RampName): [number, number, number][] {
  if (ramp === "spectral") return TURBO;
  if (ramp === "solar") return SOLAR_SCALE;
  return mode === "incident_solar" ? SOLAR_SCALE : COLD_TO_HOT;
}

const MODE_LABEL: Record<DisplayMode, string> = {
  construction: "Construction",
  surface_temperature: "Surface temperature",
  incident_solar: "Incident solar",
  interior_temperature: "Interior air (CFD)",
};

const MODE_FIELD: Record<
  Exclude<DisplayMode, "construction" | "interior_temperature">,
  "inside_face_temperature_c" | "incident_solar_w_m2"
> = {
  surface_temperature: "inside_face_temperature_c",
  incident_solar: "incident_solar_w_m2",
};

const MODE_UNIT: Record<DisplayMode, string> = {
  construction: "",
  surface_temperature: "°C",
  incident_solar: "W/m²",
  interior_temperature: "°C",
};

type Hover = { name: string; value: number | null; area: number; x: number; y: number; point?: THREE.Vector3 };

const VIEWS: ViewName[] = ["iso", "plan", "south", "east", "sun"];

const TONE_ACTIVE: Record<string, string> = {
  neutral: "border-[var(--color-ink)] bg-[var(--color-ink)] text-[var(--color-day)]",
  cold: "border-[var(--color-cold)] bg-[var(--color-cold)]/14 text-[var(--color-cold)]",
  earth: "border-[var(--color-earth)] bg-[var(--color-earth)]/14 text-[var(--color-earth)]",
};

function Chip({
  children,
  onClick,
  active = false,
  disabled = false,
  tone = "neutral",
  dashed = false,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  tone?: "neutral" | "cold" | "earth";
  dashed?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-pressed={active}
      className={`rounded-md border px-2.5 py-1 text-xs whitespace-nowrap transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40 ${
        dashed ? "border-dashed" : ""
      } ${
        active
          ? TONE_ACTIVE[tone]
          : "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-dim)] hover:border-[var(--color-ink-faint)] hover:text-[var(--color-ink)]"
      }`}
    >
      {children}
    </button>
  );
}

function Segment({
  children,
  onClick,
  active,
  disabled = false,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={`rounded-md px-2.5 py-1 text-xs whitespace-nowrap transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? "bg-[var(--color-ink)] font-medium text-[var(--color-day)]"
          : "text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]"
      }`}
    >
      {children}
    </button>
  );
}

function Divider() {
  return <span aria-hidden="true" className="h-5 w-px bg-[var(--color-line)]" />;
}

const SCENE_INK = "#26303d";

function NorthDial({ screenDeg }: { screenDeg: number }) {
  return (
    <svg viewBox="0 0 40 40" width={34} height={34} aria-hidden="true">
      <circle cx={20} cy={20} r={15} fill="rgba(255,255,255,0.82)" stroke="#5d6b7d" />
      <g transform={`rotate(${screenDeg} 20 20)`}>
        <path d="M20 6 L24 22 L20 19 L16 22 Z" fill="#b4402f" />
        <path d="M20 34 L20 24" stroke="#5d6b7d" strokeWidth={1.4} strokeLinecap="round" />
      </g>
      <text x={20} y={12} textAnchor="middle" fontSize={7.5} fontWeight={700}
            fill="#1f2733" transform={`rotate(${screenDeg} 20 20)`}>
        N
      </text>
    </svg>
  );
}

function extentOf(
  surfaces: SurfaceResults | null,
  field: "inside_face_temperature_c" | "incident_solar_w_m2",
): { min: number; max: number } | null {
  if (!surfaces) return null;
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const series of surfaces.surfaces) {
    for (const value of series[field] ?? []) {
      if (value === null || value === undefined || !Number.isFinite(value)) continue;
      if (value < min) min = value;
      if (value > max) max = value;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  if (max - min < 1e-6) return { min: min - 0.5, max: max + 0.5 };
  return { min, max };
}

function hourOf(stamp: string | undefined): number {
  if (!stamp) return 12;
  return Number(stamp.slice(11, 13)) + Number(stamp.slice(14, 16)) / 60;
}

function interpolateSun(path: SunPath | null | undefined, hour: number) {
  const positions = path?.positions;
  if (!positions || positions.length === 0) return null;
  for (let index = 0; index < positions.length - 1; index += 1) {
    const a = positions[index];
    const b = positions[index + 1];
    if (hour >= a.hour && hour <= b.hour) {
      const span = b.hour - a.hour || 1;
      const t = (hour - a.hour) / span;
      return {
        altitude: a.altitude_deg + (b.altitude_deg - a.altitude_deg) * t,
        azimuth: a.azimuth_deg + (b.azimuth_deg - a.azimuth_deg) * t,
        visible: t < 0.5 ? a.visible : b.visible,
        aboveHorizon: t < 0.5 ? a.above_horizon : b.above_horizon,
        blocked: a.horizon_altitude_deg,
      };
    }
  }
  const last = positions[positions.length - 1];
  return {
    altitude: last.altitude_deg,
    azimuth: last.azimuth_deg,
    visible: last.visible,
    aboveHorizon: last.above_horizon,
    blocked: last.horizon_altitude_deg,
  };
}

export function Scene3D({
  geometry,
  surfaces,
  sunPath,
  terrain,
  airflow,
  immersiveChrome = false,
  designOverride = null,
  badge = null,
  mode,
  onModeChange,
  hourIndex,
  onHourChange,
}: {
  geometry: GeometryPayload;
  surfaces: SurfaceResults | null;
  sunPath: SunPath | null | undefined;
  terrain: TerrainMesh | null | undefined;
  airflow?: AirflowSummary | null;
  immersiveChrome?: boolean;
  designOverride?: DesignSpec | null;
  badge?: string | null;
  mode: DisplayMode;
  onModeChange: (mode: DisplayMode) => void;
  hourIndex: number;
  onHourChange: (index: number) => void;
}) {
  const store = useStore();
  const design = designOverride ?? activeDesign(store);
  const mountRef = useRef<HTMLDivElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const cameraRef = useRef<{
    theta: number;
    phi: number;
    distance: number;
    radius: number;
  } | null>(null);
  const meshesRef = useRef<Map<string, THREE.Mesh>>(new Map());
  const sunRef = useRef<{
    light: THREE.DirectionalLight;
    disc: THREE.Group;
    setDisc: (shining: boolean) => void;
    setSky: (direction: THREE.Vector3, altitude: number) => void;
    fill: THREE.HemisphereLight;
  } | null>(null);
  const clipRef = useRef<THREE.Plane>(new THREE.Plane(new THREE.Vector3(0, 1, 0), 999));
  const raycastRef = useRef<{
    raycaster: THREE.Raycaster;
    camera: THREE.PerspectiveCamera;
  } | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const sunGroupRef = useRef<THREE.Group | null>(null);
  const gizmoRef = useRef<ReturnType<typeof createGizmos> | null>(null);
  const modeRef = useRef(mode);
  useEffect(() => { modeRef.current = mode; }, [mode]);

  const [playing, setPlaying] = useState(false);
  const [cut, setCut] = useState(false);
  const [showRooms, setShowRooms] = useState(true);
  const [shell, setShell] = useState<Shell>("solid");
  const [dressed, setDressed] = useState(true);
  const [immersive, setImmersive] = useState(false);
  const [idle, setIdle] = useState(false);
  const [elements, setElements] = useState<string[]>([]);
  const architectureRef = useRef<THREE.Group | null>(null);
  const [showAirflow, setShowAirflow] = useState(false);
  const [showDimensions, setShowDimensions] = useState(false);
  const [showFigure, setShowFigure] = useState(false);
  const [airflowLegend, setAirflowLegend] = useState<
    { label: string; detail: string }[]
  >([]);
  const airflowRef = useRef<{
    group: THREE.Group;
    tick: (delta: number) => void;
    dispose: () => void;
  } | null>(null);
  const edgeRef = useRef<EdgeLines | null>(null);
  const rigRef = useRef<CameraRig | null>(null);
  const invalidateRef = useRef<(() => void) | null>(null);
  const labelsRef = useRef<LabelLayer | null>(null);
  const figureRef = useRef<ScaleFigure | null>(null);
  const annotationsRef = useRef<THREE.Group | null>(null);
  const environmentRef = useRef<SkyEnvironment | null>(null);
  const captureRef = useRef<(() => string) | null>(null);
  const solidRef = useRef<{
    group: THREE.Group;
    parts: Map<string, THREE.Mesh[]>;
  } | null>(null);
  const [ramp, setRamp] = useState<RampName>("diverging");
  const [rooms, setRooms] = useState<
    { name: string; colour: string; area: number }[]
  >([]);
  const layoutRef = useRef<THREE.Group | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [compass, setCompass] = useState<{ metresPerPixel: number; northDeg: number } | null>(
    null,
  );
  const compassRef = useRef<{ metresPerPixel: number; northDeg: number } | null>(null);
  const [engagedHint, setEngagedHint] = useState(false);

  const thermalRef = useRef<{
    mesh: THREE.Mesh;
    update: (data: ThermalFieldData) => void;
    dispose: () => void;
    setOpacity: (value: number) => void;
    setSectionY: (worldY: number | null) => void;
  } | null>(null);
  const [cfd, setCfd] = useState<CFDFieldSet | CFDAbsent | null>(null);
  const [cfdError, setCfdError] = useState<string | null>(null);
  const [cfdLabel, setCfdLabel] = useState<string | null>(null);
  const activeField = useMemo<CFDField | null>(() => {
    if (!cfd || isCfdAbsent(cfd)) return null;
    return cfd.fields.find((item) => item.label === cfdLabel) ?? cfd.fields[0] ?? null;
  }, [cfd, cfdLabel]);
  const thermalFieldData = useMemo<ThermalFieldData | null>(
    () =>
      activeField
        ? {
            dims: activeField.dims as [number, number, number],
            bounds_min: activeField.bounds_min as [number, number, number],
            bounds_max: activeField.bounds_max as [number, number, number],
            values: activeField.temperature_c,
            min_c: activeField.min_c,
            max_c: activeField.max_c,
          }
        : null,
    [activeField],
  );
  const pipelineRef = useRef<Pipeline | null>(null);
  const qualityRef = useRef<Quality>(detectQuality());
  const [quality, setQualityState] = useState<Quality>(qualityRef.current);

  const changeQuality = (next: Quality) => {
    qualityRef.current = next;
    setQualityState(next);
    pipelineRef.current?.setQuality(next);
  };
  const [thermalOpacity, setThermalOpacity] = useState(0.6);
  const [probeTemp, setProbeTemp] = useState<{ value: number; x: number; y: number } | null>(null);


  const hours = surfaces?.timestamps ?? [];
  const maxIndex = Math.max(0, hours.length - 1);
  const safeIndex = Math.min(hourIndex, maxIndex);
  const currentStamp = hours[safeIndex];
  const clockHour = hourOf(currentStamp);

  const field = (mode === "construction" || mode === "interior_temperature") ? null : MODE_FIELD[mode];

  const valuesBySurface = useMemo(() => {
    const map = new Map<string, (number | null)[]>();
    if (!surfaces || !field) return map;
    for (const series of surfaces.surfaces) {
      map.set(series.surface, (series[field] ?? []) as (number | null)[]);
    }
    return map;
  }, [surfaces, field]);

  const areaBySurface = useMemo(() => {
    const map = new Map<string, number>();
    for (const surface of geometry.surfaces) map.set(surface.name, surface.area_m2);
    return map;
  }, [geometry]);

  const extent = useMemo(
    () => (field ? extentOf(surfaces, field) : null),
    [surfaces, field],
  );

  const sun = useMemo(() => interpolateSun(sunPath, clockHour), [sunPath, clockHour]);

  const volumeTotal = useMemo(
    () => Object.values(geometry.zone_volumes_m3 ?? {}).reduce((a, b) => a + b, 0),
    [geometry],
  );

  useEffect(() => {
    if (!playing || maxIndex === 0) return;
    const timer = window.setInterval(() => {
      onHourChange(hourIndex >= maxIndex ? 0 : hourIndex + 1);
    }, 220);
    return () => window.clearInterval(timer);
  }, [playing, maxIndex, hourIndex, onHourChange]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    sceneRef.current = scene;
    const width = mount.clientWidth || 800;
    const height = mount.clientHeight || 420;

    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      stencil: true,
      preserveDrawingBuffer: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.localClippingEnabled = true;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.78;
    mount.appendChild(renderer.domElement);
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute("role", "application");
    renderer.domElement.setAttribute(
      "aria-label",
      "Three dimensional view of the shelter. Drag or use the arrow keys to orbit, "
      + "hold shift to pan, plus and minus to zoom.",
    );
    renderer.domElement.style.touchAction = "none";

    const camera = new THREE.PerspectiveCamera(42, width / height, 0.5, 4000);
    camera.up.set(0, 0, 1);

    const labels = createLabelLayer();
    labels.resize(width, height);
    mount.appendChild(labels.element);
    labelsRef.current = labels;
    scene.add(labels.group);

    const group = new THREE.Group();
    const meshes = new Map<string, THREE.Mesh>();

    const makeOpaque = (surface: { colour: string; finish: string; opacity: number }) =>
      new THREE.MeshStandardMaterial({
        color: new THREE.Color(surface.colour),
        roughness: surface.finish === "smooth" ? 0.42 : 0.92,
        metalness: surface.finish === "smooth" ? 0.12 : 0.02,
        envMapIntensity: 0.8,
        clippingPlanes: [clipRef.current],
        clipShadows: true,
      });
    const makeGlass = (surface: { colour: string }) =>
      new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(surface.colour),
        transparent: true,
        opacity: 0.36,
        roughness: 0.05,
        metalness: 0,
        transmission: 0.75,
        thickness: 0.03,
        ior: 1.52,
        clearcoat: 1,
        clearcoatRoughness: 0.03,
        side: THREE.DoubleSide,
        clippingPlanes: [clipRef.current],
      });

    const solid = buildSolid(geometry, design ?? null, makeOpaque, makeGlass);

    for (const surface of geometry.surfaces) {
      const buffer = new THREE.BufferGeometry();
      buffer.setAttribute(
        "position",
        new THREE.BufferAttribute(new Float32Array(surface.positions), 3),
      );
      buffer.setIndex(Array.from(surface.indices));
      buffer.computeVertexNormals();
      const detail = detailMaps(surface.finish);
      const glazed = surface.finish === "transparent" || surface.opacity < 1;
      const transparent = surface.opacity < 1;
      const material = glazed
        ? new THREE.MeshPhysicalMaterial({
            color: new THREE.Color(surface.colour),
            side: THREE.DoubleSide,
            transparent: true,
            opacity: Math.min(surface.opacity, 0.42),
            roughness: 0.06,
            metalness: 0,
            transmission: 0.7,
            thickness: 0.04,
            ior: 1.52,
            reflectivity: 0.5,
            clearcoat: 1,
            clearcoatRoughness: 0.04,
            clippingPlanes: [clipRef.current],
            clipShadows: true,
          })
        : new THREE.MeshStandardMaterial({
            color: new THREE.Color(surface.colour),
            side: THREE.DoubleSide,
            transparent,
            opacity: surface.opacity,
            roughness: surface.finish === "smooth" ? 0.42 : 0.92,
            metalness: surface.finish === "smooth" ? 0.12 : 0.02,
            envMapIntensity: 0.9,
            normalMap: detail.normal,
            normalScale: new THREE.Vector2(
              surface.finish === "rough" ? 0.9 : 0.35,
              surface.finish === "rough" ? 0.9 : 0.35,
            ),
            roughnessMap: detail.rough,
            clippingPlanes: [clipRef.current],
            clipShadows: true,
          });
      const mesh = new THREE.Mesh(buffer, material);
      mesh.name = surface.name;
      mesh.userData.baseColour = surface.colour;
      mesh.castShadow = !transparent;
      mesh.receiveShadow = true;
      mesh.visible = solid === null;
      group.add(mesh);
      meshes.set(surface.name, mesh);
    }
    if (solid) {
      for (const [name, list] of solid.parts) {
        for (const mesh of list) mesh.userData.baseColour = meshes.get(name)
          ? (meshes.get(name)!.userData.baseColour as string)
          : "#ffffff";
      }
      solidRef.current = solid;
      group.add(solid.group);
    } else {
      solidRef.current = null;
    }
    meshesRef.current = meshes;

    const box = new THREE.Box3().setFromObject(group);
    const centre = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    group.position.set(-centre.x, -centre.y, 0);
    scene.add(group);

    const architecture = buildArchitecture(geometry, design ?? null, geometry.layout);
    if (architecture) {
      applyClipping(architecture.group, [clipRef.current]);
      architecture.group.position.set(-centre.x, -centre.y, 0);
      architecture.group.visible = dressed;
      architectureRef.current = architecture.group;
      setElements(architecture.elements);
      scene.add(architecture.group);
    } else {
      architectureRef.current = null;
      setElements([]);
    }

    const buildingRadius = Math.max(size.length(), 6);
    const sceneRadius = buildingRadius * 11;
    const terrainScale = terrain?.outer_radius_m
      ? sceneRadius / terrain.outer_radius_m
      : 1;

    const measured = Boolean(
      terrain && terrain.positions.length > 0 && !terrain.derived_from_horizon,
    );
    const sky = createSkyDome(sceneRadius * 3.2, 0.42);
    scene.add(sky.mesh);
    if (!measured) scene.add(createGround(sceneRadius * 1.4));
    const compassRadius = buildingRadius * 2.1;
    scene.add(createCompass(compassRadius));
    for (const mark of cardinalPoints(compassRadius * 1.07)) {
      labels.add(mark.text, mark.position, "cardinal",
                 "True north, the bearing the simulation places the sun against.");
    }

    if (terrain && terrain.positions.length > 0) {
      const mesh = createTerrain(
        Array.from(terrain.positions),
        Array.from(terrain.indices),
        terrainScale,
        measured,
      );
      if (mesh) {
        mesh.castShadow = false;
        scene.add(mesh);
      }
    }

    if (geometry.layout) {
      const built = createLayout(
        geometry.layout,
        geometry.surfaces.find(
          (item) => item.kind === "floor" && item.zone.toUpperCase() === "ROOM",
        ) ?? geometry.surfaces.find((item) => item.kind === "floor"),
      );
      built.group.position.set(-centre.x, -centre.y, 0);
      layoutRef.current = built.group;
      setRooms(built.rooms);
      scene.add(built.group);
    } else {
      layoutRef.current = null;
      setRooms([]);
    }

    const outlineSource = solid ? [...solid.parts.values()].flat() : [...meshes.values()];
    const lines = createEdgeLines(outlineSource, {
      colour: SCENE_INK,
      widthPx: 1.4,
      threshold: 24,
      opacity: 0.62,
      clippingPlanes: [clipRef.current],
    });
    if (lines) {
      lines.object.position.copy(group.position);
      lines.setResolution(width * renderer.getPixelRatio(),
                          height * renderer.getPixelRatio());
      edgeRef.current = lines;
      scene.add(lines.object);
    } else {
      edgeRef.current = null;
    }

    const annotations = new THREE.Group();
    annotations.position.copy(group.position);
    annotations.visible = false;
    const dimensionLabels: CSS2DObject[] = [];
    if (design) {
      const planWidth = Math.sqrt(design.plan.floor_area_m2 * design.plan.aspect_ratio);
      const planDepth = Math.sqrt(design.plan.floor_area_m2 / design.plan.aspect_ratio);
      const dimensions = dimensionLines(planWidth, planDepth,
                                        design.plan.ceiling_height_m,
                                        SCENE_INK);
      dimensions.object.position.set(centre.x, centre.y, 0);
      annotations.add(dimensions.object);
      const shift = new THREE.Vector3(centre.x, centre.y, 0);
      for (const label of dimensions.labels) {
        const object = labels.add(label.text, label.position.clone().add(shift),
                                  "dimension", label.title);
        object.position.add(annotations.position);
        object.visible = false;
        dimensionLabels.push(object);
      }
    }
    annotations.userData.labels = dimensionLabels;
    annotationsRef.current = annotations;
    scene.add(annotations);

    const figure = createScaleFigure(
      new THREE.Vector3(size.x / 2 + 1.2, -size.y / 2 - 1.0, 0),
      SCENE_INK,
    );
    figure.group.visible = false;
    figureRef.current = figure;
    scene.add(figure.group);

    if (airflow && ((airflow.zones?.length ?? 0) > 0
                    || (airflow.couplings?.length ?? 0) > 0)) {
      if (airflowRef.current) airflowRef.current.dispose();
      const built = createParticleAirflow(
        airflow,
        geometry.layout,
        { x: -centre.x, y: -centre.y },
        Math.max(size.z, 2),
      );
      built.group.visible = false;
      airflowRef.current = built;
      setAirflowLegend(built.legend);
      scene.add(built.group);
    } else {
      if (airflowRef.current) airflowRef.current.dispose();
      airflowRef.current = null;
      setAirflowLegend([]);
    }

    const sunGroup = new THREE.Group();
    sunGroupRef.current = sunGroup;
    scene.add(sunGroup);
    if (sunPath) {
      sunGroup.add(createSunPathArc(Array.from(sunPath.positions), sceneRadius * 0.8));
    }

    if (design) {
      const handleWidth = Math.sqrt(design.plan.floor_area_m2 * design.plan.aspect_ratio);
      const handleDepth = Math.sqrt(design.plan.floor_area_m2 / design.plan.aspect_ratio);
      const gizmos = createGizmos(handleWidth, handleDepth, design.plan.ceiling_height_m);
      gizmos.group.position.set(-centre.x, -centre.y, 0);
      gizmos.group.visible = modeRef.current === "construction";
      gizmoRef.current = gizmos;
      scene.add(gizmos.group);
    } else {
      gizmoRef.current = null;
    }

    const sunDisc = createSunDisc(buildingRadius * 0.2);
    scene.add(sunDisc.group);

    const shadowPixels = qualityRef.current === "high" ? 4096 : 2048;
    const light = new THREE.DirectionalLight(0xfff2dd, 1.85);
    light.castShadow = true;
    light.shadow.mapSize.set(shadowPixels, shadowPixels);
    const shadowSpan = buildingRadius * 1.5;
    light.shadow.camera.left = -shadowSpan;
    light.shadow.camera.right = shadowSpan;
    light.shadow.camera.top = shadowSpan;
    light.shadow.camera.bottom = -shadowSpan;
    light.shadow.camera.near = 1;
    light.shadow.camera.far = sceneRadius * 3;
    light.shadow.bias = -0.0004;
    light.shadow.normalBias = 0.02;
    light.userData.radius = sceneRadius * 0.85;
    scene.add(light);
    scene.add(light.target);

    const fill = new THREE.HemisphereLight(0xdfe9f5, 0x6b6350, 0.55);
    scene.add(fill);

    const bounce = new THREE.DirectionalLight(0xcfe0f2, 0.22);
    bounce.position.set(-1, -0.6, 0.5);
    scene.add(bounce);

    const environment = createSkyEnvironment(renderer);
    environmentRef.current = environment;
    scene.environmentIntensity = 0.42;
    scene.environment = environment.refresh(new THREE.Vector3(0.3, -0.6, 0.7), 35);

    sunRef.current = {
      light, disc: sunDisc.group, setDisc: sunDisc.setState,
      setSky: sky.setSun, fill,
    };

    let needsRender = true;
    const invalidate = () => {
      needsRender = true;
    };
    invalidateRef.current = invalidate;

    const target = new THREE.Vector3(0, 0, size.z * 0.35);
    const remembered = cameraRef.current;
    const rig = createCameraRig(camera, renderer.domElement, target,
                                { min: buildingRadius * 0.55, max: sceneRadius * 1.6 },
                                invalidate);
    rigRef.current = rig;
    rig.restore(
      remembered
        ? {
            theta: remembered.theta,
            phi: remembered.phi,
            distance: remembered.distance * (buildingRadius / remembered.radius),
          }
        : { ...orbitFor("iso", null), distance: buildingRadius * 1.85 },
    );
    raycastRef.current = { raycaster: new THREE.Raycaster(), camera };

    let activeGizmo: {
      handle: HandleType;
      mesh: THREE.Mesh;
      startPoint: THREE.Vector3;
      startDesign: DesignSpec;
      plane: THREE.Plane;
    } | null = null;
    let orbiting = false;

    const pointer = new THREE.Vector2();
    const readPointer = (event: PointerEvent): DOMRect => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      return rect;
    };
    const dragDelta = (point: THREE.Vector3): number => {
      if (!activeGizmo) return 0;
      if (activeGizmo.handle === "east") return point.x - activeGizmo.startPoint.x;
      if (activeGizmo.handle === "west") return -(point.x - activeGizmo.startPoint.x);
      if (activeGizmo.handle === "north") return point.y - activeGizmo.startPoint.y;
      if (activeGizmo.handle === "south") return -(point.y - activeGizmo.startPoint.y);
      if (activeGizmo.handle === "top") return point.z - activeGizmo.startPoint.z;
      return 0;
    };

    const onMove = (event: PointerEvent) => {
      const rect = readPointer(event);
      const kit = raycastRef.current;
      if (!kit) return;

      if (activeGizmo && gizmoRef.current) {
        kit.raycaster.setFromCamera(pointer, kit.camera);
        const current = new THREE.Vector3();
        if (kit.raycaster.ray.intersectPlane(activeGizmo.plane, current)) {
          const next = computeNewDesign(activeGizmo.startDesign, activeGizmo.handle,
                                        dragDelta(current));
          gizmoRef.current.updateGhost(
            Math.sqrt(next.plan.floor_area_m2 * next.plan.aspect_ratio),
            Math.sqrt(next.plan.floor_area_m2 / next.plan.aspect_ratio),
            next.plan.ceiling_height_m,
          );
          invalidate();
        }
        return;
      }

      if (orbiting) {
        setHover(null);
        return;
      }

      kit.raycaster.setFromCamera(pointer, kit.camera);

      if (modeRef.current === "construction" && gizmoRef.current) {
        const handles = kit.raycaster.intersectObjects(gizmoRef.current.handles, false);
        gizmoRef.current.handles.forEach((handle) => {
          (handle.material as THREE.MeshBasicMaterial).color.setHex(0xffffff);
        });
        if (handles.length > 0) {
          ((handles[0].object as THREE.Mesh).material as THREE.MeshBasicMaterial)
            .color.setHex(0xffa500);
        }
        invalidate();
      }

      const pickable: THREE.Object3D[] = solid
        ? [...solid.parts.values()].flat()
        : [...meshes.values()];
      const hits = kit.raycaster.intersectObjects(pickable, false);
      if (hits.length === 0) {
        setHover(null);
        return;
      }
      const hit = hits[0];
      setHover({
        name: hit.object.name,
        value: null,
        area: areaBySurface.get(hit.object.name) ?? 0,
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
        point: hit.point,
      });
    };

    const onDown = (event: PointerEvent) => {
      readPointer(event);
      rig.setZoomEnabled(true);
      setEngagedHint(true);
      const kit = raycastRef.current;
      if (modeRef.current === "construction" && gizmoRef.current && design && kit) {
        kit.raycaster.setFromCamera(pointer, kit.camera);
        const hits = kit.raycaster.intersectObjects(gizmoRef.current.handles, false);
        if (hits.length > 0) {
          const hit = hits[0];
          const mesh = hit.object as THREE.Mesh;
          const type = mesh.userData.type as HandleType;
          const normal = type === "top"
            ? new THREE.Vector3(0, 1, 0)
            : new THREE.Vector3(0, 0, 1);
          activeGizmo = {
            handle: type,
            mesh,
            startPoint: hit.point,
            startDesign: design,
            plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal, hit.point),
          };
          rig.controls.enabled = false;
          gizmoRef.current.ghost.visible = true;
          (mesh.material as THREE.MeshBasicMaterial).color.setHex(0xffa500);
          renderer.domElement.setPointerCapture(event.pointerId);
          invalidate();
          return;
        }
      }
      orbiting = true;
    };

    const onUp = (event: PointerEvent) => {
      orbiting = false;
      if (!activeGizmo || !gizmoRef.current) return;
      const kit = raycastRef.current;
      if (kit) {
        readPointer(event);
        kit.raycaster.setFromCamera(pointer, kit.camera);
        const current = new THREE.Vector3();
        if (kit.raycaster.ray.intersectPlane(activeGizmo.plane, current)) {
          store.applyOverride(computeNewDesign(activeGizmo.startDesign, activeGizmo.handle,
                                               dragDelta(current)));
        }
      }
      (activeGizmo.mesh.material as THREE.MeshBasicMaterial).color.setHex(0xffffff);
      gizmoRef.current.ghost.visible = false;
      activeGizmo = null;
      rig.controls.enabled = true;
      try {
        renderer.domElement.releasePointerCapture(event.pointerId);
      } catch {
        /* the pointer was already released */
      }
      invalidate();
    };

    const onLeave = () => {
      setHover(null);
      orbiting = false;
      rig.setZoomEnabled(false);
      setEngagedHint(false);
    };
    const onKey = (event: KeyboardEvent) => {
      const step = event.shiftKey ? 0.02 : 0.08;
      const current = rig.state();
      if (event.key === "ArrowLeft") rig.goTo({ ...current, theta: current.theta + step });
      else if (event.key === "ArrowRight") rig.goTo({ ...current, theta: current.theta - step });
      else if (event.key === "ArrowUp") rig.goTo({ ...current, phi: current.phi - step });
      else if (event.key === "ArrowDown") rig.goTo({ ...current, phi: current.phi + step });
      else if (event.key === "+" || event.key === "=") {
        rig.goTo({ ...current, distance: current.distance * 0.88 });
      } else if (event.key === "-" || event.key === "_") {
        rig.goTo({ ...current, distance: current.distance * 1.14 });
      } else return;
      event.preventDefault();
      invalidate();
    };
    const onFocus = () => rig.setZoomEnabled(true);
    const onBlur = () => rig.setZoomEnabled(false);

    renderer.domElement.addEventListener("pointerdown", onDown, true);
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerup", onUp);
    renderer.domElement.addEventListener("pointerleave", onLeave);
    renderer.domElement.addEventListener("keydown", onKey);
    renderer.domElement.addEventListener("focus", onFocus);
    renderer.domElement.addEventListener("blur", onBlur);

    const pipeline = createPipeline(renderer, scene, camera, width, height,
                                    qualityRef.current);
    pipelineRef.current = pipeline;

    captureRef.current = () => {
      pipeline.render();
      const source = renderer.domElement;
      const flat = document.createElement("canvas");
      flat.width = source.width;
      flat.height = source.height;
      const context = flat.getContext("2d");
      if (!context) return source.toDataURL("image/png");
      context.drawImage(source, 0, 0);
      labels.paint(context, camera, flat.width, flat.height, renderer.getPixelRatio());
      if (compassRef.current) {
        paintHud(context, flat.width, flat.height, renderer.getPixelRatio(),
                 compassRef.current);
      }
      return flat.toDataURL("image/png");
    };

    const resize = () => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (!w || !h) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      pipeline.resize(w, h);
      labels.resize(w, h);
      edgeRef.current?.setResolution(w * renderer.getPixelRatio(),
                                     h * renderer.getPixelRatio());
      invalidate();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    let lastTime = performance.now();
    let lastCompass = 0;
    let frame = 0;
    const animate = () => {
      const now = performance.now();
      const delta = Math.min((now - lastTime) / 1000, 0.1);
      lastTime = now;

      if (rig.tick(delta)) needsRender = true;
      if (airflowRef.current && airflowRef.current.group.visible) {
        airflowRef.current.tick(delta);
        needsRender = true;
      }
      if (figureRef.current?.group.visible) figureRef.current.faceCamera(camera);
      if (needsRender) {
        needsRender = false;
        pipeline.render();
        labels.render(scene, camera);
        if (now - lastCompass > 120) {
          lastCompass = now;
          const span = 2 * camera.position.distanceTo(rig.controls.target)
            * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
          const centre = rig.controls.target.clone().project(camera);
          const north = rig.controls.target.clone()
            .add(new THREE.Vector3(0, size.y || 1, 0)).project(camera);
          const reading = {
            metresPerPixel: span / Math.max(mount.clientHeight, 1),
            northDeg: THREE.MathUtils.radToDeg(
              Math.atan2(north.x - centre.x, north.y - centre.y)),
          };
          compassRef.current = reading;
          setCompass(reading);
        }
      }
      frame = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(frame);
      cameraRef.current = { ...rig.state(), radius: buildingRadius };
      rig.dispose();
      rigRef.current = null;
      pipeline.dispose();
      pipelineRef.current = null;
      captureRef.current = null;
      invalidateRef.current = null;
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown, true);
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("pointerleave", onLeave);
      renderer.domElement.removeEventListener("keydown", onKey);
      renderer.domElement.removeEventListener("focus", onFocus);
      renderer.domElement.removeEventListener("blur", onBlur);
      if (gizmoRef.current) {
        gizmoRef.current.dispose();
        gizmoRef.current = null;
      }
      edgeRef.current?.dispose();
      edgeRef.current = null;
      figureRef.current?.dispose();
      figureRef.current = null;
      annotationsRef.current = null;
      environment.dispose();
      environmentRef.current = null;
      labels.dispose();
      labelsRef.current = null;
      disposeObject(scene);
      renderer.forceContextLoss();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
      sunRef.current = null;
      raycastRef.current = null;
      sceneRef.current = null;
    };
  }, [geometry, design, terrain, sunPath, airflow, areaBySurface]);

  useEffect(() => {
    const kit = sunRef.current;
    if (!kit || !sun) return;
    const bounds = kit.light.userData.radius as number;
    const position = sunVector(Math.max(sun.altitude, -5), sun.azimuth, bounds);
    kit.light.position.copy(position);
    kit.light.target.position.set(0, 0, 0);
    kit.light.target.updateMatrixWorld();
    kit.disc.position.copy(position);
    const shining = sun.visible && sun.altitude > 0;
    kit.light.intensity = shining ? 2.0 : 0.0;
    kit.fill.intensity = sun.aboveHorizon ? 0.7 : 0.35;
    kit.setDisc(shining);
    kit.disc.visible = sun.altitude > -3;
    kit.setSky(position, sun.altitude);
    if (environmentRef.current && sceneRef.current) {
      sceneRef.current.environment = environmentRef.current.refresh(position, sun.altitude);
    }
    invalidateRef.current?.();
  }, [sun, terrain]);

  useEffect(() => {
    const stops = rampStops(mode, ramp);
    const paint = (mesh: THREE.Mesh, name: string) => {
      const material = mesh.material as THREE.MeshStandardMaterial;
      if (!material.color) return;
      if (mode === "construction" || !extent) {
        material.color.set(mesh.userData.baseColour as string);
        return;
      }
      const value = valuesBySurface.get(name)?.[safeIndex];
      if (value === null || value === undefined || !Number.isFinite(value)) {
        material.color.set("#aeb5bd");
        return;
      }
      material.color.set(
        lerpColour((value - extent.min) / (extent.max - extent.min), stops),
      );
    };
    meshesRef.current.forEach((mesh, name) => paint(mesh, name));
    const solid = solidRef.current;
    if (solid) {
      for (const [name, list] of solid.parts) {
        for (const mesh of list) paint(mesh, name);
      }
    }
  }, [mode, ramp, safeIndex, valuesBySurface, extent, geometry]);

  useEffect(() => {
    if (layoutRef.current) layoutRef.current.visible = showRooms;
  }, [showRooms, geometry]);

  useEffect(() => {
    if (architectureRef.current) architectureRef.current.visible = dressed;
  }, [dressed, geometry]);

  useEffect(() => {
    if (airflowRef.current) airflowRef.current.group.visible = showAirflow;
  }, [showAirflow, geometry, airflow]);

  useEffect(() => {
    if (edgeRef.current) {
      edgeRef.current.object.visible = true;
      edgeRef.current.setWidth(shell === "wireframe" ? 1.9 : 1.1);
      edgeRef.current.setOpacity(shell === "solid" ? 0.34 : 0.7);
    }
    const targets: THREE.Mesh[] = [];
    const solid = solidRef.current;
    if (solid) {
      for (const list of solid.parts.values()) targets.push(...list);
    } else {
      meshesRef.current.forEach((mesh) => targets.push(mesh));
    }
    targets.forEach((mesh) => {
      const material = mesh.material as THREE.MeshStandardMaterial;
      if (shell === "wireframe") {
        material.transparent = true;
        material.opacity = 0.06;
        material.depthWrite = false;
      } else if (shell === "xray") {
        material.transparent = true;
        material.opacity = 0.3;
        material.depthWrite = false;
      } else {
        material.transparent = false;
        material.opacity = 1;
        material.depthWrite = true;
      }
      material.needsUpdate = true;
    });
  }, [shell, geometry, mode]);

  useEffect(() => {
    clipRef.current.constant = cut ? 0.01 : 9999;
    const face = (material: THREE.Material) => {
      if (material.userData.side === undefined) material.userData.side = material.side;
      material.side = cut ? THREE.DoubleSide : (material.userData.side as THREE.Side);
      material.needsUpdate = true;
    };
    const solid = solidRef.current;
    if (solid) {
      for (const list of solid.parts.values()) {
        for (const mesh of list) face(mesh.material as THREE.Material);
      }
    } else {
      meshesRef.current.forEach((mesh) => face(mesh.material as THREE.Material));
    }
    architectureRef.current?.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (mesh.material && !Array.isArray(mesh.material)) face(mesh.material);
    });
  }, [cut, geometry]);

  useEffect(() => {
    if (!annotationsRef.current) return;
    annotationsRef.current.visible = showDimensions;
    for (const label of (annotationsRef.current.userData.labels as CSS2DObject[]) ?? []) {
      label.visible = showDimensions;
    }
  }, [showDimensions, geometry, design]);

  useEffect(() => {
    if (figureRef.current) figureRef.current.group.visible = showFigure;
  }, [showFigure, geometry]);

  useEffect(() => {
    invalidateRef.current?.();
  });

  const hoverValue = hover ? valuesBySurface.get(hover.name)?.[safeIndex] : undefined;
  const painted = mode !== "construction";
  const unit = MODE_UNIT[mode];

  useEffect(() => {
    if (mode !== "interior_temperature") return;
    let cancelled = false;
    setCfd(null);
    setCfdError(null);
    api.cfd(geometry.design_hash).then(
      (value) => {
        if (!cancelled) setCfd(value);
      },
      (error: unknown) => {
        if (!cancelled) setCfdError(error instanceof Error ? error.message : String(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [mode, geometry.design_hash]);

  // Create or update the thermal volume mesh
  useEffect(() => {
    if (mode !== "interior_temperature" || !thermalFieldData || !sceneRef.current) {
      if (thermalRef.current) {
        if (sceneRef.current) sceneRef.current.remove(thermalRef.current.mesh);
        thermalRef.current.dispose();
        thermalRef.current = null;
      }
      return;
    }
    
    const stops = rampStops(mode, ramp);
    if (!thermalRef.current) {
      const volume = createThermalVolume(thermalFieldData, stops);
      volume.setOpacity(thermalOpacity);
      sceneRef.current.add(volume.mesh);
      thermalRef.current = volume;
    } else {
      thermalRef.current.update(thermalFieldData);
      thermalRef.current.setOpacity(thermalOpacity);
    }
    
    thermalRef.current?.setSectionY(cut ? 0.01 : null);
  }, [mode, thermalFieldData, ramp, thermalOpacity, cut, geometry]);

  useEffect(() => {
    if (gizmoRef.current) {
      gizmoRef.current.group.visible = mode === "construction";
    }
  }, [mode]);

  // Visibility management / shell adjustment
  useEffect(() => {
    if (mode === "interior_temperature") {
      setShell("xray");
    } else if (shell === "xray") {
      setShell("solid");
    }
  }, [mode]);

  useEffect(() => {
    if (mode !== "interior_temperature" || !hover?.point || !thermalFieldData) {
      setProbeTemp(null);
      return;
    }
    const temp = probeTemperature(thermalFieldData, hover.point);
    if (temp !== null) {
      setProbeTemp({ value: temp, x: hover.x, y: hover.y });
    } else {
      setProbeTemp(null);
    }
  }, [hover, mode, thermalFieldData]);

  const noonIndex = useCallback((): number | null => {
    if (!sunPath || hours.length === 0) return null;
    let best = 0;
    let bestGap = Infinity;
    hours.forEach((stamp, index) => {
      const gap = Math.abs(hourOf(stamp) - sunPath.noon.hour);
      if (gap < bestGap) {
        bestGap = gap;
        best = index;
      }
    });
    return best;
  }, [sunPath, hours]);

  const jumpToNoon = useCallback(() => {
    const index = noonIndex();
    if (index !== null) onHourChange(index);
  }, [noonIndex, onHourChange]);

  useEffect(() => {
    if (store.hourSeeded) return;
    const index = noonIndex();
    if (index !== null) store.seedHour(index);
  }, [noonIndex, store]);

  const applyView = useCallback(
    (view: ViewName) => {
      const rig = rigRef.current;
      if (!rig) return;
      rig.goTo({ ...orbitFor(view, sun), distance: rig.state().distance });
      invalidateRef.current?.();
    },
    [sun],
  );

  const saveImage = useCallback(() => {
    const capture = captureRef.current;
    if (!capture) return;
    const link = document.createElement("a");
    link.href = capture();
    const stamp = (currentStamp ?? "").replace(/[:T]/g, "-") || "view";
    link.download = `entropy-zero-${geometry.arrangement}-${mode}-${stamp}.png`;
    link.click();
  }, [currentStamp, geometry.arrangement, mode]);

  const toggleImmersive = useCallback(() => {
    const shell = shellRef.current;
    if (!shell) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => undefined);
    } else {
      void shell.requestFullscreen().catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    if (!immersive) {
      setIdle(false);
      return;
    }
    let timer = window.setTimeout(() => setIdle(true), 2600);
    const wake = () => {
      setIdle(false);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setIdle(true), 2600);
    };
    window.addEventListener("pointermove", wake);
    window.addEventListener("pointerdown", wake);
    window.addEventListener("wheel", wake, { passive: true });
    window.addEventListener("keydown", wake);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("pointermove", wake);
      window.removeEventListener("pointerdown", wake);
      window.removeEventListener("wheel", wake);
      window.removeEventListener("keydown", wake);
    };
  }, [immersive]);

  useEffect(() => {
    const onChange = () => setImmersive(Boolean(document.fullscreenElement));
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName)) return;
      if (event.key === "f" || event.key === "F") toggleImmersive();
    };
    document.addEventListener("fullscreenchange", onChange);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      window.removeEventListener("keydown", onKey);
    };
  }, [toggleImmersive]);

  return (
    <div
      ref={shellRef}
      className={
        immersive
          ? "flex h-screen flex-col bg-black"
          : immersiveChrome
            ? "relative"
            : "space-y-2"
      }
    >
      <div
        className={
          immersive
            ? `absolute inset-x-4 top-4 z-20 flex items-start justify-between gap-3 rounded-xl border border-[var(--color-line)]/40 bg-[var(--color-night-2)]/80 p-2 shadow-2xl backdrop-blur-md transition-opacity duration-300 ${
                idle ? "pointer-events-none opacity-0" : "opacity-100"
              }`
            : immersiveChrome
              ? "absolute inset-x-3 top-3 z-10 flex items-start justify-between gap-3 rounded-xl border border-[var(--color-line)]/50 bg-[var(--color-night-2)]/75 p-2 shadow-lg backdrop-blur-md"
              : "flex items-start justify-between gap-3"
        }
      >
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-2">
          <div
            role="group"
            aria-label="What is painted on the surfaces"
            className="flex items-center rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] p-0.5"
          >
            {(Object.keys(MODE_LABEL) as DisplayMode[])
              .filter((item) => item !== "interior_temperature")
              .map((item) => (
                <Segment
                  key={item}
                  active={mode === item}
                  disabled={item !== "construction" && !surfaces}
                  onClick={() => onModeChange(item)}
                >
                  {MODE_LABEL[item]}
                </Segment>
              ))}
          </div>
          <Chip
            tone="earth"
            dashed
            active={mode === "interior_temperature"}
            onClick={() => onModeChange("interior_temperature")}
            title="Air temperature solved by ANSYS Fluent for one hour, shown only when that run exists for this design."
          >
            {MODE_LABEL.interior_temperature}
          </Chip>
          {mode !== "construction" && (
            <select
              value={ramp}
              onChange={(event) => setRamp(event.target.value as RampName)}
              className="rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)] px-2 py-1 text-xs text-[var(--color-ink-dim)]"
              aria-label="Colour scale"
            >
              {(Object.keys(RAMP_LABEL) as RampName[]).map((item) => (
                <option key={item} value={item}>
                  {RAMP_LABEL[item]}
                </option>
              ))}
            </select>
          )}

          <Divider />

          <div
            role="group"
            aria-label="How the shell is drawn"
            className="flex items-center rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] p-0.5"
          >
            {(Object.keys(SHELL_LABEL) as Shell[]).map((item) => (
              <Segment key={item} active={shell === item} onClick={() => setShell(item)}>
                {SHELL_LABEL[item]}
              </Segment>
            ))}
          </div>
          <Chip
            active={cut}
            onClick={() => setCut((value) => !value)}
            title="Cut the south half away on the north to south centre line and look straight into the room. What you see at the cut is the inside face of the construction, not a filled solid."
          >
            Section
          </Chip>
          <Chip
            active={dressed}
            onClick={() => setDressed((value) => !value)}
            title="Plinth, parapet, door and steps. Construction detail drawn from the design; it carries no result and is never painted with one."
          >
            Built detail
          </Chip>
          {geometry.layout && (
            <Chip active={showRooms} onClick={() => setShowRooms((value) => !value)}>
              Rooms
            </Chip>
          )}

          <Divider />

          {airflow && ((airflow.zones?.length ?? 0) > 0
                       || (airflow.couplings?.length ?? 0) > 0) && (
            <Chip
              tone="cold"
              active={showAirflow}
              onClick={() => setShowAirflow((value) => !value)}
            >
              Air exchange
            </Chip>
          )}
          {design && (
            <Chip
              active={showDimensions}
              onClick={() => setShowDimensions((value) => !value)}
              title="Outside width and depth and the floor to ceiling height, read straight off the design you entered."
            >
              Dimensions
            </Chip>
          )}
          <Chip
            active={showFigure}
            onClick={() => setShowFigure((value) => !value)}
            title={`A ${FIGURE_HEIGHT_M.toFixed(2)} m standing figure, drawn only to give the model a sense of scale. It is not an occupant in the simulation.`}
          >
            Scale figure
          </Chip>
          {mode === "interior_temperature" && (
            <label className="flex items-center gap-1.5 text-[11px] text-[var(--color-ink-faint)]">
              Volume
              <input
                type="range"
                min={0.1}
                max={1.0}
                step={0.05}
                value={thermalOpacity}
                onChange={(event) => setThermalOpacity(Number(event.target.value))}
                className="w-20 accent-[var(--color-earth)]"
                aria-label="How solid the air volume is drawn"
              />
            </label>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <select
            value={quality}
            onChange={(event) => changeQuality(event.target.value as Quality)}
            className="rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)] px-2 py-1 text-xs text-[var(--color-ink-dim)]"
            aria-label="Render quality"
            title="Ambient occlusion, bloom and antialiasing cost frame rate. Drop this if the view stutters."
          >
            <option value="high">High render</option>
            <option value="balanced">Balanced</option>
            <option value="plain">Plain</option>
          </select>
          <Chip onClick={saveImage} title="Save what is on screen as a PNG.">
            Save image
          </Chip>
          <Chip onClick={toggleImmersive} title="Fill the screen. Press F to toggle.">
            {immersive ? "Exit full screen" : "Full screen"}
          </Chip>
        </div>
      </div>

      {!immersive && !immersiveChrome && (
        <p className="text-xs text-[var(--color-ink-faint)]">
          {engagedHint
            ? "Drag to orbit, right drag to pan, scroll to zoom. Hover a face for its name and value."
            : "Click or tab into the view to take control. Then drag to orbit, right drag to pan, scroll or the arrow keys to move."}
        </p>
      )}

      <div className={immersive ? "relative min-h-0 flex-1" : "relative"}>
        <div
          ref={mountRef}
          className={`relative w-full overflow-hidden bg-[#070b11] ${
            immersive
              ? "h-full"
              : "rounded-xl border border-[#1b2836] shadow-[inset_0_0_60px_rgba(0,0,0,0.55)]"
          } ${
            immersive
              ? ""
              : immersiveChrome
                ? "h-[clamp(520px,78vh,900px)]"
                : "h-[clamp(420px,62vh,760px)]"
          }`}
        />

        {badge && (
          <div
            key={badge}
            role="status"
            aria-live="polite"
            className="badge-flash pointer-events-none absolute bottom-4 left-4 z-10 max-w-[70%] rounded-lg border border-[var(--color-line)]/50 bg-[var(--color-night-2)]/85 px-3 py-2 text-xs font-medium text-[var(--color-ink)] shadow-lg backdrop-blur-md"
          >
            {badge}
          </div>
        )}

        <div
          role="group"
          aria-label="Camera position"
          className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-xl border border-[var(--color-line)]/50 bg-[var(--color-night-2)]/80 p-1 shadow-lg backdrop-blur-md"
        >
          {VIEWS.map((view) => (
            <button
              key={view}
              type="button"
              onClick={() => applyView(view)}
              disabled={view === "sun" && !(sun && sun.altitude > 0)}
              title={VIEW_HINT[view]}
              className="rounded-lg px-2.5 py-1 text-[11px] whitespace-nowrap text-[var(--color-ink-dim)] transition-colors duration-150 hover:bg-[var(--color-night-3)] hover:text-[var(--color-ink)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              {VIEW_LABEL[view]}
            </button>
          ))}
        </div>

        {compass && (() => {
          const bar = scaleBar(compass.metresPerPixel);
          return (
            <div className="pointer-events-none absolute bottom-4 right-4 z-10 flex items-end gap-3">
              {bar && (
                <div className="flex flex-col items-start gap-1">
                  <span className="tnum rounded bg-white/80 px-1.5 py-0.5 text-[10px] font-semibold text-[#1f2733]">
                    {barLabel(bar.metres)}
                  </span>
                  <svg
                    width={Math.round(bar.pixels)}
                    height={10}
                    viewBox={`0 0 ${Math.round(bar.pixels)} 10`}
                    aria-hidden="true"
                  >
                    <rect x={0} y={3} width={Math.round(bar.pixels) / 2} height={4}
                          fill="#1f2733" />
                    <rect x={Math.round(bar.pixels) / 2} y={3}
                          width={Math.round(bar.pixels) / 2} height={4}
                          fill="rgba(255,255,255,0.9)" stroke="#1f2733" strokeWidth={0.8} />
                    <path d={`M0 0 V10 M${Math.round(bar.pixels)} 0 V10`} stroke="#1f2733"
                          strokeWidth={1.2} />
                  </svg>
                </div>
              )}
              <NorthDial screenDeg={compass.northDeg} />
            </div>
          );
        })()}

        <div className="pointer-events-none absolute inset-0 rounded-lg">
          <span className="absolute left-2 top-2 h-4 w-4 border-l border-t border-[#2f4a63]" />
          <span className="absolute right-2 top-2 h-4 w-4 border-r border-t border-[#2f4a63]" />
          <span className="absolute bottom-2 left-2 h-4 w-4 border-b border-l border-[#2f4a63]" />
          <span className="absolute bottom-2 right-2 h-4 w-4 border-b border-r border-[#2f4a63]" />
        </div>

        <div className="pointer-events-none absolute left-4 top-4 flex flex-col gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-[#6f97b3]">
          <span className="text-[13px] normal-case tracking-normal text-[#dbe9f4]">
            {geometry.arrangement.replace(/_/g, " ")}
          </span>
          <span className="tabular-nums">
            {geometry.surfaces.length} surfaces ·{" "}
            {num(volumeTotal, 1)} m³
          </span>
          <span className="tabular-nums">
            azimuth {num(geometry.azimuth_deg, 0)}°
          </span>
        </div>

        <div className="pointer-events-none absolute right-4 top-4 flex flex-col items-end gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-[#6f97b3]">
          <span className="text-[#dbe9f4]">{MODE_LABEL[mode]}</span>
          <span className="tabular-nums">
            alt {num(sun?.altitude, 1)}° · az {num(sun?.azimuth, 1)}°
          </span>
          <span>{quality}</span>
        </div>

        {hover && (
          <div
            className="pointer-events-none absolute z-10 rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)]/95 px-2 py-1 text-[11px] shadow-sm"
            style={{
              left: Math.min(hover.x + 12, 620),
              top: Math.max(hover.y - 10, 4),
            }}
          >
            <div className="font-mono font-medium">{hover.name}</div>
            <div className="text-[var(--color-ink-faint)]">
              {num(hover.area, 2)} m²
              {mode !== "interior_temperature" && painted && hoverValue !== undefined && hoverValue !== null
                ? ` · ${num(hoverValue)} ${unit}`
                : ""}
            </div>
            {mode === "interior_temperature" && probeTemp && (
              <div className="text-[var(--color-solar-ink)] font-medium mt-0.5">
                Air: {num(probeTemp.value, 1)} °C
              </div>
            )}
          </div>
        )}

        {sun && (
          <div className="pointer-events-none absolute left-3 top-3 rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)]/90 px-2.5 py-1.5 text-[11px] leading-relaxed">
            <div className="font-medium">
              {currentStamp ? currentStamp.replace("T", " ") : "—"}
            </div>
            <div className="text-[var(--color-ink-faint)]">
              sun {num(sun.altitude)}° above, {num(sun.azimuth)}° from north
            </div>
            <div
              className={
                sun.aboveHorizon && !sun.visible
                  ? "text-[var(--color-diesel)]"
                  : "text-[var(--color-ink-faint)]"
              }
            >
              {!sun.aboveHorizon
                ? "below the horizon"
                : sun.visible
                  ? "clear of the ridge"
                  : `behind the ridge, blocked to ${num(sun.blocked)}°`}
            </div>
          </div>
        )}
      </div>

      {sunPath && (
        <div className="grid gap-2 rounded-lg border border-[var(--color-line)] bg-slate-50 p-3 text-xs sm:grid-cols-3">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-[var(--color-ink-faint)]">
              Astronomical daylight
            </div>
            <div className="tabular-nums">
              {num(sunPath.daylight_hours, 1)} h
              <span className="ml-1 text-[var(--color-ink-faint)]">
                {sunPath.astronomical_sunrise_hour !== null &&
                sunPath.astronomical_sunrise_hour !== undefined
                  ? `(${num(sunPath.astronomical_sunrise_hour, 2)} to ${num(
                      sunPath.astronomical_sunset_hour ?? 0,
                      2,
                    )})`
                  : ""}
              </span>
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-[var(--color-ink-faint)]">
              Sun actually clear of the ridge
            </div>
            <div className="tabular-nums">
              {num(sunPath.visible_daylight_hours, 1)} h
              <span className="ml-1 text-[var(--color-ink-faint)]">
                {sunPath.visible_sunrise_hour !== null &&
                sunPath.visible_sunrise_hour !== undefined
                  ? `(${num(sunPath.visible_sunrise_hour, 2)} to ${num(
                      sunPath.visible_sunset_hour ?? 0,
                      2,
                    )})`
                  : ""}
              </span>
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-[var(--color-ink-faint)]">
              Lost to the valley
            </div>
            <div
              className={`tabular-nums ${
                sunPath.daylight_lost_to_terrain_h > 0
                  ? "font-semibold text-[var(--color-diesel)]"
                  : ""
              }`}
            >
              {num(sunPath.daylight_lost_to_terrain_h, 1)} h
              <span className="ml-1 font-normal text-[var(--color-ink-faint)]">
                {sunPath.has_horizon
                  ? "of collecting time on this date"
                  : "no horizon profile supplied"}
              </span>
            </div>
          </div>
        </div>
      )}

      {hours.length > 0 && (
        <div className="flex items-center gap-3">
          <Chip
            active={playing}
            onClick={() => setPlaying((value) => !value)}
            title="Step through every simulated hour in the run period."
          >
            {playing ? "Pause" : "Play"}
          </Chip>
          <Chip onClick={jumpToNoon} title="Jump to the hour nearest solar noon.">
            Solar noon
          </Chip>
          <input
            type="range"
            min={0}
            max={maxIndex}
            value={safeIndex}
            onChange={(event) => {
              setPlaying(false);
              onHourChange(Number(event.target.value));
            }}
            aria-label="Simulated hour"
            className="flex-1 accent-[var(--color-solar)]"
          />
          <span className="w-40 text-right text-xs tabular-nums text-[var(--color-ink-faint)]">
            {currentStamp?.replace("T", " ") ?? "—"}
          </span>
        </div>
      )}

      {!immersive && geometry.layout && showRooms && rooms.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className="font-medium text-[var(--color-ink)]">
              {geometry.layout.label}
            </span>
            {rooms.map((room) => (
              <span
                key={room.name}
                className="flex items-center gap-1.5 text-[var(--color-ink-faint)]"
              >
                <i
                  className="inline-block h-2.5 w-2.5 rounded-sm"
                  style={{ background: room.colour }}
                />
                {room.name}
                <span className="tabular-nums">{num(room.area, 1)} m²</span>
              </span>
            ))}
          </div>
          <p className="text-xs text-[var(--color-ink-faint)]">
            {geometry.layout.thermal_zoning}{" "}
            {num(geometry.layout.partition_area_m2, 1)} m² of partition enters the
            simulation as internal mass.
            {(geometry.layout.notes?.length ?? 0) > 1
              ? ` ${geometry.layout.notes[0]}`
              : ""}
          </p>
        </div>
      )}

      {mode === "interior_temperature" && (
        <div className="space-y-2 rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)]/60 p-3 text-xs leading-relaxed text-[var(--color-ink-faint)]">
          {cfdError ? (
            <p>
              <span className="font-medium text-[var(--color-ink)]">
                The solved air field could not be loaded.
              </span>{" "}
              {cfdError}
            </p>
          ) : !cfd ? (
            <p>Looking for a solved air field for this design…</p>
          ) : isCfdAbsent(cfd) ? (
            <p>
              <span className="font-medium text-[var(--color-ink)]">
                No solved air field for this design, so nothing is drawn inside the room.
              </span>{" "}
              {cfd.reason}
            </p>
          ) : activeField ? (
            <>
              {cfd.fields.length > 1 && (
                <div role="group" aria-label="Solved hour" className="flex flex-wrap gap-1">
                  {cfd.fields.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      onClick={() => setCfdLabel(item.label)}
                      aria-pressed={item.label === activeField.label}
                      className={`rounded-md border px-2 py-0.5 ${
                        item.label === activeField.label
                          ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-white"
                          : "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-faint)]"
                      }`}
                    >
                      {item.label.replace(/_/g, " ")}
                    </button>
                  ))}
                </div>
              )}
              {!activeField.solver.converged && (
                <p className="font-medium text-[var(--color-earth)]">
                  The solver did not converge on this case. Do not quote numbers from it.
                </p>
              )}
              <p>
                <span className="font-medium text-[var(--color-ink)]">
                  Solved by {activeField.solver.solver} {activeField.solver.version}
                </span>{" "}
                for {activeField.label.replace(/_/g, " ")} at{" "}
                {activeField.timestamp.replace("T", " ")}: air {num(activeField.min_c)} to{" "}
                {num(activeField.max_c)} °C, mean {num(activeField.mean_c)} °C, against the{" "}
                {num(activeField.energyplus_zone_air_c)} °C EnergyPlus reported for the room as
                one well mixed volume. {activeField.solver.turbulence_model},{" "}
                {activeField.solver.cell_count.toLocaleString()} cells. Each voxel is the
                nearest solver cell and empty voxels lie outside the air.{" "}
                {activeField.solver.averaging_s != null
                  ? `The air at this hour never settles, so this is its average over ${num(activeField.solver.averaging_s, 0)} s of simulated flow.`
                  : "This is a steady solution for that hour."}{" "}
                The timeline does not move it.
              </p>
            </>
          ) : null}
          {cfd && (cfd.rejected ?? []).length > 0 && (
            <ul className="space-y-0.5">
              {(cfd.rejected ?? []).map((item) => (
                <li key={item}>· {item}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!immersive && showAirflow && airflow && (
        <div className="space-y-1.5 rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)]/60 p-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <span className="font-medium text-[var(--color-ink)]">Air exchange</span>
            {airflowLegend.map((item) => (
              <span key={item.label} className="text-[var(--color-ink-faint)]">
                <span className="text-[var(--color-ink-dim)]">{item.label}</span>{" "}
                {item.detail}
              </span>
            ))}
          </div>
          <p className="text-xs text-[var(--color-ink-faint)]">{airflow.note}</p>
        </div>
      )}

      {!immersive && dressed && elements.length > 0 && (
        <p className="text-xs leading-relaxed text-[var(--color-ink-faint)]">
          Drawn as it would be built: {elements.join(", ")}. These are construction
          elements derived from the design, not simulated surfaces. Nothing here is
          painted with a result, and the faces that carry results are unchanged.
        </p>
      )}

      {!immersive && mode === "interior_temperature" && activeField && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-ink-faint)]">
          <span className="tabular-nums">{num(activeField.min_c)} °C</span>
          <span
            className="h-2.5 flex-1 rounded"
            style={{ background: rampCss(rampStops(mode, ramp)) }}
          />
          <span className="tabular-nums">{num(activeField.max_c)} °C</span>
        </div>
      )}

      {!immersive && painted && mode !== "interior_temperature" && extent && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-ink-faint)]">
          <span className="tabular-nums">
            {num(extent.min)} {unit}
          </span>
          <span
            className="h-2.5 flex-1 rounded"
            style={{ background: rampCss(rampStops(mode, ramp)) }}
          />
          <span className="tabular-nums">
            {num(extent.max)} {unit}
          </span>
        </div>
      )}

      <p className="text-xs leading-relaxed text-[var(--color-ink-faint)]">
        {mode === "interior_temperature"
          ? "The air volume is drawn only from an ANSYS Fluent run for this exact design; the page never interpolates or estimates air temperature on its own."
          : painted
            ? "Every painted face is one number EnergyPlus reported for that surface at that hour, and the face names come from the simulation, so the value sits on the surface it belongs to."
            : "Material colours come from the Layer 0 database. The geometry drawn here is the geometry that was simulated, built from one description."}{" "}
        {terrain && terrain.positions.length > 0
          ? terrain.derived_from_horizon
            ? "The skyline is the terrain horizon table the simulation shades with, so what blocks the sun on screen is what blocked it in the run."
            : `The ground is the measured elevation model, ${terrain.vertex_count.toLocaleString()} points out to ${Math.round((terrain.outer_radius_m ?? 0) / 1000)} km, shaded by height above the site. It is the same ground the horizon profile the simulation shades with was measured from, and it is scaled uniformly so every skyline angle is the angle the run used. Heights are not exaggerated, and the colour is a display ramp over real elevation rather than observed snow.`
          : "No terrain is attached, so the simulation assumed a flat skyline in every direction."}{" "}
        {mode !== "interior_temperature" && "There is no air temperature or velocity field here because the engine computes neither; those live in the detailed analysis tab and come from a fluid dynamics solver."}
      </p>
    </div>
  );
}
