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
import { buildArchitecture } from "../lib/architecture";
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
  detailMaps,
  detectQuality,
  type Pipeline,
  type Quality,
} from "../lib/render_pipeline";
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
  const clipRef = useRef<THREE.Plane>(new THREE.Plane(new THREE.Vector3(0, -1, 0), 999));
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
  const [airflowLegend, setAirflowLegend] = useState<
    { label: string; detail: string }[]
  >([]);
  const airflowRef = useRef<{
    group: THREE.Group;
    tick: (delta: number) => void;
    dispose: () => void;
  } | null>(null);
  const edgesRef = useRef<THREE.Group | null>(null);
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
  const [engagedHint, setEngagedHint] = useState(false);

  const thermalRef = useRef<{
    mesh: THREE.Mesh;
    update: (data: ThermalFieldData) => void;
    dispose: () => void;
    setOpacity: (v: number) => void;
    setClipHeight: (h: number) => void;
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

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.localClippingEnabled = true;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.92;
    mount.appendChild(renderer.domElement);

    const camera = new THREE.PerspectiveCamera(42, width / height, 0.5, 4000);

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

      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(buffer, 20),
        new THREE.LineBasicMaterial({
          color: 0x1c2530,
          transparent: true,
          opacity: 0.4,
          clippingPlanes: [clipRef.current],
        }),
      );
      edges.visible = solid === null;
      group.add(edges);
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
    const architecture = buildArchitecture(
      geometry,
      design ?? null,
      geometry.layout,
    );
    if (architecture) {
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
    const sky = createSkyDome(sceneRadius * 3.2);
    scene.add(sky.mesh);
    scene.add(createGround(measured ? buildingRadius * 1.9 : sceneRadius * 1.4));
    scene.add(createCompass(buildingRadius * 2.1));

    if (terrain && terrain.positions.length > 0) {
      const mesh = createTerrain(
        Array.from(terrain.positions),
        Array.from(terrain.indices),
        terrainScale,
        measured,
      );
      if (mesh) scene.add(mesh);
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

    const edges = new THREE.Group();
    for (const mesh of meshes.values()) {
      const line = new THREE.LineSegments(
        new THREE.EdgesGeometry(mesh.geometry, 25),
        new THREE.LineBasicMaterial({ color: 0x1e293b, transparent: true,
                                      opacity: 0.75 }),
      );
      line.position.copy(mesh.position);
      line.rotation.copy(mesh.rotation);
      edges.add(line);
    }
    edges.position.copy(group.position);
    edges.visible = false;
    edgesRef.current = edges;
    scene.add(edges);

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
      const width = Math.sqrt(design.plan.floor_area_m2 * design.plan.aspect_ratio);
      const depth = Math.sqrt(design.plan.floor_area_m2 / design.plan.aspect_ratio);
      const height = design.plan.ceiling_height_m;
      const gizmos = createGizmos(width, depth, height);
      gizmos.group.position.set(-centre.x, -centre.y, 0);
      gizmos.group.visible = modeRef.current === "construction";
      gizmoRef.current = gizmos;
      scene.add(gizmos.group);
    } else {
      gizmoRef.current = null;
    }

    const sunDisc = createSunDisc(buildingRadius * 0.2);
    scene.add(sunDisc.group);

    const light = new THREE.DirectionalLight(0xfff2dd, 2.1);
    light.castShadow = true;
    light.shadow.mapSize.set(2048, 2048);
    const shadowSpan = buildingRadius * 2.4;
    light.shadow.camera.left = -shadowSpan;
    light.shadow.camera.right = shadowSpan;
    light.shadow.camera.top = shadowSpan;
    light.shadow.camera.bottom = -shadowSpan;
    light.shadow.camera.near = 1;
    light.shadow.camera.far = sceneRadius * 3;
    light.shadow.bias = -0.0006;
    light.userData.radius = sceneRadius * 0.85;
    scene.add(light);
    scene.add(light.target);

    const fill = new THREE.HemisphereLight(0xdfe9f5, 0x6b6350, 0.9);
    scene.add(fill);

    const bounce = new THREE.DirectionalLight(0xcfe0f2, 0.35);
    bounce.position.set(-1, -0.6, 0.5);
    scene.add(bounce);

    const environment = new THREE.PMREMGenerator(renderer);
    environment.compileEquirectangularShader();
    const envScene = new THREE.Scene();
    const envSky = new THREE.Mesh(
      new THREE.SphereGeometry(10, 24, 16),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color("#b9cfe4"),
        side: THREE.BackSide,
      }),
    );
    envScene.add(envSky);
    const envGround = new THREE.Mesh(
      new THREE.CircleGeometry(10, 32),
      new THREE.MeshBasicMaterial({ color: new THREE.Color("#a9976f") }),
    );
    envGround.rotation.x = -Math.PI / 2;
    envGround.position.y = -1.5;
    envScene.add(envGround);
    const envTarget = environment.fromScene(envScene, 0.04);
    scene.environment = envTarget.texture;
    envSky.geometry.dispose();
    envGround.geometry.dispose();
    environment.dispose();
    sunRef.current = {
      light, disc: sunDisc.group, setDisc: sunDisc.setState,
      setSky: sky.setSun, fill,
    };

    const remembered = cameraRef.current;
    let theta = remembered ? remembered.theta : Math.PI * 0.78;
    let phi = remembered ? remembered.phi : Math.PI * 0.435;
    let distance = remembered
      ? remembered.distance * (buildingRadius / remembered.radius)
      : buildingRadius * 2.5;
    let dragging = false;
    let engaged = false;
    let lastX = 0;
    let lastY = 0;

    const place = () => {
      cameraRef.current = { theta, phi, distance, radius: buildingRadius };
      camera.position.set(
        distance * Math.sin(phi) * Math.sin(theta),
        distance * Math.sin(phi) * Math.cos(theta),
        Math.max(distance * Math.cos(phi), 1.2),
      );
      camera.up.set(0, 0, 1);
      camera.lookAt(0, 0, size.z * 0.35);
    };
    place();
    raycastRef.current = { raycaster: new THREE.Raycaster(), camera };

    let activeGizmo: {
      handle: HandleType;
      mesh: THREE.Mesh;
      startPoint: THREE.Vector3;
      startDesign: DesignSpec;
      plane: THREE.Plane;
    } | null = null;

    const pointer = new THREE.Vector2();
    const onMove = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      if (activeGizmo && gizmoRef.current) {
        const kit = raycastRef.current;
        if (kit) {
          kit.raycaster.setFromCamera(pointer, kit.camera);
          const currentPoint = new THREE.Vector3();
          kit.raycaster.ray.intersectPlane(activeGizmo.plane, currentPoint);
          if (currentPoint) {
            let delta = 0;
            if (activeGizmo.handle === "east") delta = currentPoint.x - activeGizmo.startPoint.x;
            else if (activeGizmo.handle === "west") delta = -(currentPoint.x - activeGizmo.startPoint.x);
            else if (activeGizmo.handle === "north") delta = currentPoint.y - activeGizmo.startPoint.y;
            else if (activeGizmo.handle === "south") delta = -(currentPoint.y - activeGizmo.startPoint.y);
            else if (activeGizmo.handle === "top") delta = currentPoint.z - activeGizmo.startPoint.z;

            const newDesign = computeNewDesign(activeGizmo.startDesign, activeGizmo.handle, delta);
            const w = Math.sqrt(newDesign.plan.floor_area_m2 * newDesign.plan.aspect_ratio);
            const d = Math.sqrt(newDesign.plan.floor_area_m2 / newDesign.plan.aspect_ratio);
            const h = newDesign.plan.ceiling_height_m;
            gizmoRef.current.updateGhost(w, d, h);
          }
        }
        return;
      }

      if (modeRef.current === "construction" && gizmoRef.current && !dragging) {
        const kit = raycastRef.current;
        if (kit) {
          kit.raycaster.setFromCamera(pointer, kit.camera);
          const hits = kit.raycaster.intersectObjects(gizmoRef.current.handles, false);
          gizmoRef.current.handles.forEach(h => {
             (h.material as THREE.MeshBasicMaterial).color.setHex(0xffffff);
          });
          if (hits.length > 0) {
            const mat = (hits[0].object as THREE.Mesh).material as THREE.MeshBasicMaterial;
            mat.color.setHex(0xffa500);
          }
        }
      }

      if (dragging) {
        theta -= (event.clientX - lastX) * 0.007;
        phi = THREE.MathUtils.clamp(phi - (event.clientY - lastY) * 0.007, 0.08, 1.52);
        lastX = event.clientX;
        lastY = event.clientY;
        place();
        setHover(null);
        return;
      }
      const kit = raycastRef.current;
      if (!kit) return;
      kit.raycaster.setFromCamera(pointer, kit.camera);
      const pickable: THREE.Object3D[] = solid
        ? [...solid.parts.values()].flat()
        : [...meshes.values()];
      const hits = kit.raycaster.intersectObjects(pickable, false);
      if (hits.length === 0) {
        setHover(null);
        return;
      }
      const hit = hits[0];
      const name = hit.object.name;
      setHover({
        name,
        value: null,
        area: areaBySurface.get(name) ?? 0,
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
        point: hit.point,
      });
    };
    const onDown = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      if (modeRef.current === "construction" && gizmoRef.current && design) {
        const kit = raycastRef.current;
        if (kit) {
          kit.raycaster.setFromCamera(pointer, kit.camera);
          const hits = kit.raycaster.intersectObjects(gizmoRef.current.handles, false);
          if (hits.length > 0) {
            const hit = hits[0];
            const mesh = hit.object as THREE.Mesh;
            const type = mesh.userData.type as HandleType;
            const normal = type === "top" ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
            
            activeGizmo = {
              handle: type,
              mesh,
              startPoint: hit.point,
              startDesign: design,
              plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal, hit.point)
            };
            
            gizmoRef.current.ghost.visible = true;
            (mesh.material as THREE.MeshBasicMaterial).color.setHex(0xffa500);
            dragging = true;
            engaged = true;
            setEngagedHint(true);
            renderer.domElement.setPointerCapture(event.pointerId);
            return;
          }
        }
      }

      dragging = true;
      engaged = true;
      setEngagedHint(true);
      lastX = event.clientX;
      lastY = event.clientY;
      renderer.domElement.setPointerCapture(event.pointerId);
    };
    const onUp = (event: PointerEvent) => {
      dragging = false;
      try {
        renderer.domElement.releasePointerCapture(event.pointerId);
      } catch {
        /* pointer already released */
      }

      if (activeGizmo && gizmoRef.current) {
        const kit = raycastRef.current;
        if (kit) {
          const rect = renderer.domElement.getBoundingClientRect();
          pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
          pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
          kit.raycaster.setFromCamera(pointer, kit.camera);
          
          const currentPoint = new THREE.Vector3();
          kit.raycaster.ray.intersectPlane(activeGizmo.plane, currentPoint);
          if (currentPoint) {
            let delta = 0;
            if (activeGizmo.handle === "east") delta = currentPoint.x - activeGizmo.startPoint.x;
            else if (activeGizmo.handle === "west") delta = -(currentPoint.x - activeGizmo.startPoint.x);
            else if (activeGizmo.handle === "north") delta = currentPoint.y - activeGizmo.startPoint.y;
            else if (activeGizmo.handle === "south") delta = -(currentPoint.y - activeGizmo.startPoint.y);
            else if (activeGizmo.handle === "top") delta = currentPoint.z - activeGizmo.startPoint.z;

            const newDesign = computeNewDesign(activeGizmo.startDesign, activeGizmo.handle, delta);
            store.applyOverride(newDesign);
          }
        }
        
        (activeGizmo.mesh.material as THREE.MeshBasicMaterial).color.setHex(0xffffff);
        gizmoRef.current.ghost.visible = false;
        activeGizmo = null;
        return;
      }
    };
    const onLeave = () => {
      setHover(null);
      engaged = false;
      setEngagedHint(false);
    };
    const onWheel = (event: WheelEvent) => {
      if (!engaged) return;
      event.preventDefault();
      distance = THREE.MathUtils.clamp(
        distance * (1 + event.deltaY * 0.0012),
        buildingRadius * 0.9,
        sceneRadius * 1.6,
      );
      place();
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerup", onUp);
    renderer.domElement.addEventListener("pointerleave", onLeave);
    renderer.domElement.addEventListener("wheel", onWheel, { passive: false });

    const resize = () => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (!w || !h) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      pipeline.resize(w, h);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    const pipeline = createPipeline(renderer, scene, camera, width, height,
                                    qualityRef.current);
    pipelineRef.current = pipeline;

    let lastTime = performance.now();
    let frame = 0;
    const animate = () => {
      const now = performance.now();
      const delta = (now - lastTime) / 1000;
      lastTime = now;

      if (airflowRef.current && airflowRef.current.group.visible) {
        airflowRef.current.tick(delta);
      }

      pipeline.render();
      frame = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(frame);
      pipeline.dispose();
      pipelineRef.current = null;
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("pointerleave", onLeave);
      renderer.domElement.removeEventListener("wheel", onWheel);
      if (gizmoRef.current) {
        gizmoRef.current.dispose();
        gizmoRef.current = null;
      }
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
    kit.light.intensity = shining ? 2.4 : 0.0;
    kit.fill.intensity = sun.aboveHorizon ? 1.2 : 0.5;
    kit.setDisc(shining);
    kit.disc.visible = sun.altitude > -3;
    kit.setSky(position, sun.altitude);
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
    if (edgesRef.current) edgesRef.current.visible = shell !== "solid";
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
    const box = new THREE.Box3();
    geometry.surfaces.forEach((surface) => {
      for (let index = 0; index < surface.positions.length; index += 3) {
        box.expandByPoint(
          new THREE.Vector3(
            surface.positions[index],
            surface.positions[index + 1],
            surface.positions[index + 2],
          ),
        );
      }
    });
    const centre = box.getCenter(new THREE.Vector3());
    clipRef.current.constant = cut ? -(0 - centre.y) + 0.01 : 9999;
  }, [cut, geometry]);

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
    
    // Handle clip height
    if (thermalRef.current) {
      let maxZ = 9999;
      if (cut) {
        const box = new THREE.Box3();
        geometry.surfaces.forEach((surface) => {
          for (let index = 0; index < surface.positions.length; index += 3) {
            box.expandByPoint(
              new THREE.Vector3(
                surface.positions[index],
                surface.positions[index + 1],
                surface.positions[index + 2],
              ),
            );
          }
        });
        const centre = box.getCenter(new THREE.Vector3());
        maxZ = centre.z;
      }
      thermalRef.current.setClipHeight(maxZ);
    }
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

  const jumpToNoon = useCallback(() => {
    if (!sunPath || hours.length === 0) return;
    let best = 0;
    let bestGap = Infinity;
    hours.forEach((stamp, index) => {
      const gap = Math.abs(hourOf(stamp) - sunPath.noon.hour);
      if (gap < bestGap) {
        bestGap = gap;
        best = index;
      }
    });
    onHourChange(best);
  }, [sunPath, hours, onHourChange]);

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
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-2">
        <div
          role="group"
          aria-label="What is painted on the surfaces"
          className="flex items-center rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] p-0.5"
        >
          {(Object.keys(MODE_LABEL) as DisplayMode[])
            .filter((item) => item !== "interior_temperature")
            .map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => onModeChange(item)}
                disabled={item !== "construction" && !surfaces}
                aria-pressed={mode === item}
                className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                  mode === item
                    ? "bg-[var(--color-ink)] font-medium text-white"
                    : "text-[var(--color-ink-faint)] hover:text-[var(--color-ink)] disabled:opacity-40"
                }`}
              >
                {MODE_LABEL[item]}
              </button>
            ))}
        </div>
        <button
          type="button"
          onClick={() => onModeChange("interior_temperature")}
          aria-pressed={mode === "interior_temperature"}
          title="Air temperature solved by ANSYS Fluent for one hour, shown only when that run exists for this design."
          className={`rounded-md border border-dashed px-2.5 py-1 text-xs transition-colors ${
            mode === "interior_temperature"
              ? "border-[var(--color-earth)] bg-[var(--color-earth)]/12 font-medium text-[var(--color-earth)]"
              : "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-faint)] hover:text-[var(--color-ink)] disabled:opacity-40"
          }`}
        >
          {MODE_LABEL.interior_temperature}
        </button>
        <button
          type="button"
          onClick={() => setCut((value) => !value)}
          className={`rounded-md border px-2.5 py-1 text-xs ${
            cut
              ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-white"
              : "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-faint)]"
          }`}
        >
          Section
        </button>
        <div
          role="group"
          aria-label="How the shell is drawn"
          className="flex items-center rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] p-0.5"
        >
          {(Object.keys(SHELL_LABEL) as Shell[]).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setShell(item)}
              aria-pressed={shell === item}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                shell === item
                  ? "bg-[var(--color-ink)] font-medium text-white"
                  : "text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]"
              }`}
            >
              {SHELL_LABEL[item]}
            </button>
          ))}
        </div>
        {airflow && ((airflow.zones?.length ?? 0) > 0
                     || (airflow.couplings?.length ?? 0) > 0) && (
          <button
            type="button"
            onClick={() => setShowAirflow((value) => !value)}
            className={`rounded-md border px-2.5 py-1 text-xs ${
              showAirflow
                ? "border-[var(--color-cold)] bg-[var(--color-cold)] text-white"
                : "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-faint)]"
            }`}
          >
            Air exchange
          </button>
        )}
        <button
          type="button"
          onClick={() => setDressed((value) => !value)}
          className={`rounded-md border px-2.5 py-1 text-xs ${
            dressed
              ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-white"
              : "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-faint)]"
          }`}
          title="Plinth, parapet, door and steps. Construction detail drawn from the design; it carries no result and is never painted with one."
        >
          Built detail
        </button>
        {geometry.layout && (
          <button
            type="button"
            onClick={() => setShowRooms((value) => !value)}
            className={`rounded-md border px-2.5 py-1 text-xs ${
              showRooms
                ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-white"
                : "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-faint)]"
            }`}
          >
            Rooms
          </button>
        )}
        <select
          value={quality}
          onChange={(event) => changeQuality(event.target.value as Quality)}
          className="rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)] px-2 py-1 text-xs text-[var(--color-ink-faint)]"
          aria-label="Render quality"
          title="Ambient occlusion, bloom and antialiasing cost frame rate. Drop this if the view stutters."
        >
          <option value="high">High render</option>
          <option value="balanced">Balanced</option>
          <option value="plain">Plain</option>
        </select>
        {mode !== "construction" && (
          <select
            value={ramp}
            onChange={(event) => setRamp(event.target.value as RampName)}
            className="rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)] px-2 py-1 text-xs text-[var(--color-ink-faint)]"
            aria-label="Colour scale"
          >
            {(Object.keys(RAMP_LABEL) as RampName[]).map((item) => (
              <option key={item} value={item}>
                {RAMP_LABEL[item]}
              </option>
            ))}
          </select>
        )}
        {!immersive && mode === "interior_temperature" && (
          <input
            type="range"
            min={0.1}
            max={1.0}
            step={0.05}
            value={thermalOpacity}
            onChange={(e) => setThermalOpacity(Number(e.target.value))}
            className="w-24 accent-[var(--color-ink)]"
            title="Volume Opacity"
          />
        )}
        {hours.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setPlaying((value) => !value)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                playing
                  ? "border-[var(--color-solar)] bg-[var(--color-solar)] text-white"
                  : "border-[var(--color-line)] bg-[var(--color-night-2)] text-[var(--color-ink-faint)]"
              }`}
            >
              {playing ? "Pause" : "Play 24 h"}
            </button>
            <button
              type="button"
              onClick={jumpToNoon}
              className="rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)] px-2.5 py-1 text-xs text-[var(--color-ink-faint)]"
            >
              Solar noon
            </button>
          </>
        )}
        </div>

        <button
          type="button"
          onClick={toggleImmersive}
          className="shrink-0 rounded-md border border-[var(--color-line)] bg-[var(--color-night-2)] px-2.5 py-1 text-xs whitespace-nowrap text-[var(--color-ink-faint)] transition-colors hover:border-[var(--color-ink-faint)] hover:text-[var(--color-ink)]"
          title="Fill the screen. Press F to toggle."
        >
          {immersive ? "Exit full screen" : "Full screen"}
        </button>
      </div>

      {!immersive && !immersiveChrome && (
        <p className="text-xs text-[var(--color-ink-faint)]">
          {engagedHint
            ? "drag to orbit, scroll to zoom, hover a face"
            : "click the view to take control, then drag and scroll"}
        </p>
      )}

      <div className={immersive ? "relative min-h-0 flex-1" : "relative"}>
        <div
          ref={mountRef}
          className={`w-full overflow-hidden bg-[#070b11] ${
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
          <input
            type="range"
            min={0}
            max={maxIndex}
            value={safeIndex}
            onChange={(event) => {
              setPlaying(false);
              onHourChange(Number(event.target.value));
            }}
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
