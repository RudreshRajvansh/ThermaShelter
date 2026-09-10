import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type {
  AirflowSummary,
  GeometryPayload,
  SunPath,
  SurfaceResults,
  TerrainMesh,
} from "../lib/api";
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
  createSky,
  createSunDisc,
  createSunPathArc,
  createAirflow,
  createLayout,
  createTerrain,
  disposeObject,
  makeLabel,
  sunVector,
} from "../lib/scene";

export type DisplayMode = "construction" | "surface_temperature" | "incident_solar";

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
};

const MODE_FIELD: Record<
  Exclude<DisplayMode, "construction">,
  "inside_face_temperature_c" | "incident_solar_w_m2"
> = {
  surface_temperature: "inside_face_temperature_c",
  incident_solar: "incident_solar_w_m2",
};

const MODE_UNIT: Record<DisplayMode, string> = {
  construction: "",
  surface_temperature: "°C",
  incident_solar: "W/m²",
};

type Hover = { name: string; value: number | null; area: number; x: number; y: number };

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
  if (!path || path.positions.length === 0) return null;
  const positions = path.positions;
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
  mode: DisplayMode;
  onModeChange: (mode: DisplayMode) => void;
  hourIndex: number;
  onHourChange: (index: number) => void;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
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

  const [playing, setPlaying] = useState(false);
  const [cut, setCut] = useState(false);
  const [showRooms, setShowRooms] = useState(true);
  const [shell, setShell] = useState<Shell>("solid");
  const [showAirflow, setShowAirflow] = useState(false);
  const [airflowLegend, setAirflowLegend] = useState<
    { label: string; detail: string }[]
  >([]);
  const airflowRef = useRef<THREE.Group | null>(null);
  const edgesRef = useRef<THREE.Group | null>(null);
  const [ramp, setRamp] = useState<RampName>("diverging");
  const [rooms, setRooms] = useState<
    { name: string; colour: string; area: number }[]
  >([]);
  const layoutRef = useRef<THREE.Group | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [engagedHint, setEngagedHint] = useState(false);

  const hours = surfaces?.timestamps ?? [];
  const maxIndex = Math.max(0, hours.length - 1);
  const safeIndex = Math.min(hourIndex, maxIndex);
  const currentStamp = hours[safeIndex];
  const clockHour = hourOf(currentStamp);

  const field = mode === "construction" ? null : MODE_FIELD[mode];

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
    renderer.localClippingEnabled = true;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;
    mount.appendChild(renderer.domElement);

    const camera = new THREE.PerspectiveCamera(42, width / height, 0.5, 4000);

    const group = new THREE.Group();
    const meshes = new Map<string, THREE.Mesh>();
    for (const surface of geometry.surfaces) {
      const buffer = new THREE.BufferGeometry();
      buffer.setAttribute(
        "position",
        new THREE.BufferAttribute(new Float32Array(surface.positions), 3),
      );
      buffer.setIndex(Array.from(surface.indices));
      buffer.computeVertexNormals();
      const transparent = surface.opacity < 1;
      const material = new THREE.MeshLambertMaterial({
        color: new THREE.Color(surface.colour),
        side: THREE.DoubleSide,
        transparent,
        opacity: surface.opacity,
        clippingPlanes: [clipRef.current],
        clipShadows: true,
      });
      const mesh = new THREE.Mesh(buffer, material);
      mesh.name = surface.name;
      mesh.userData.baseColour = surface.colour;
      mesh.castShadow = !transparent;
      mesh.receiveShadow = true;
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
      group.add(edges);
    }
    meshesRef.current = meshes;

    const box = new THREE.Box3().setFromObject(group);
    const centre = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    group.position.set(-centre.x, -centre.y, 0);
    scene.add(group);

    const buildingRadius = Math.max(size.length(), 6);
    const sceneRadius = buildingRadius * 11;
    const terrainScale = terrain?.outer_radius_m
      ? sceneRadius / terrain.outer_radius_m
      : 1;

    const measured = Boolean(
      terrain && terrain.positions.length > 0 && !terrain.derived_from_horizon,
    );
    const sky = createSky(sceneRadius * 3.2);
    scene.add(sky.mesh);
    scene.add(createGround(measured ? buildingRadius * 1.9 : sceneRadius * 1.4));
    scene.add(createCompass(buildingRadius * 2.1));

    for (const [text, azimuth] of [
      ["N", 0],
      ["E", 90],
      ["S", 180],
      ["W", 270],
    ] as [string, number][]) {
      const label = makeLabel(text, text === "N" ? "#c53030" : "#3d4753");
      const angle = THREE.MathUtils.degToRad(azimuth);
      label.position.set(
        buildingRadius * 2.35 * Math.sin(angle),
        buildingRadius * 2.35 * Math.cos(angle),
        1.2,
      );
      label.scale.set(buildingRadius * 0.42, buildingRadius * 0.21, 1);
      scene.add(label);
    }

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
      const built = createLayout(geometry.layout, {
        x: -centre.x,
        y: -centre.y,
      });
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

    if (airflow && (airflow.zones.length > 0 || airflow.couplings.length > 0)) {
      const built = createAirflow(
        airflow,
        geometry.layout,
        { x: -centre.x, y: -centre.y },
        Math.max(size.z, 2),
      );
      built.group.visible = false;
      airflowRef.current = built.group;
      setAirflowLegend(built.legend);
      scene.add(built.group);
    } else {
      airflowRef.current = null;
      setAirflowLegend([]);
    }

    const sunGroup = new THREE.Group();
    sunGroupRef.current = sunGroup;
    scene.add(sunGroup);
    if (sunPath) {
      sunGroup.add(createSunPathArc(Array.from(sunPath.positions), sceneRadius * 0.8));
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

    const fill = new THREE.HemisphereLight(0xdfe9f5, 0x6b6350, 1.15);
    scene.add(fill);
    sunRef.current = {
      light, disc: sunDisc.group, setDisc: sunDisc.setState,
      setSky: sky.setSun, fill,
    };

    let theta = Math.PI * 0.86;
    let phi = Math.PI * 0.27;
    let distance = buildingRadius * 3.0;
    let dragging = false;
    let engaged = false;
    let lastX = 0;
    let lastY = 0;

    const place = () => {
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

    const pointer = new THREE.Vector2();
    const onMove = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
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
      const hits = kit.raycaster.intersectObjects([...meshes.values()], false);
      if (hits.length === 0) {
        setHover(null);
        return;
      }
      const name = hits[0].object.name;
      setHover({
        name,
        value: null,
        area: areaBySurface.get(name) ?? 0,
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    };
    const onDown = (event: PointerEvent) => {
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
      renderer.setSize(w, h);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    let frame = 0;
    const animate = () => {
      renderer.render(scene, camera);
      frame = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("pointerleave", onLeave);
      renderer.domElement.removeEventListener("wheel", onWheel);
      disposeObject(scene);
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
      sunRef.current = null;
      raycastRef.current = null;
      sceneRef.current = null;
    };
  }, [geometry, terrain, sunPath, airflow, areaBySurface]);

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
    const meshes = meshesRef.current;
    const stops = rampStops(mode, ramp);
    meshes.forEach((mesh, name) => {
      const material = mesh.material as THREE.MeshLambertMaterial;
      if (mode === "construction" || !extent) {
        material.color.set(mesh.userData.baseColour as string);
        return;
      }
      const value = valuesBySurface.get(name)?.[safeIndex];
      if (value === null || value === undefined || !Number.isFinite(value)) {
        material.color.set("#aeb5bd");
        return;
      }
      material.color.set(lerpColour((value - extent.min) / (extent.max - extent.min), stops));
    });
  }, [mode, ramp, safeIndex, valuesBySurface, extent, geometry]);

  useEffect(() => {
    if (layoutRef.current) layoutRef.current.visible = showRooms;
  }, [showRooms, geometry]);

  useEffect(() => {
    if (airflowRef.current) airflowRef.current.visible = showAirflow;
  }, [showAirflow, geometry, airflow]);

  useEffect(() => {
    const meshes = meshesRef.current;
    if (edgesRef.current) edgesRef.current.visible = shell !== "solid";
    meshes.forEach((mesh) => {
      const material = mesh.material as THREE.MeshLambertMaterial;
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

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {(Object.keys(MODE_LABEL) as DisplayMode[]).map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => onModeChange(item)}
            disabled={item !== "construction" && !surfaces}
            className={`rounded-md border px-2.5 py-1 text-xs transition ${
              mode === item
                ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-white"
                : "border-[var(--color-line)] bg-white text-[var(--color-ink-faint)] hover:border-[var(--color-ink-faint)] disabled:opacity-40"
            }`}
          >
            {MODE_LABEL[item]}
          </button>
        ))}
        <span className="mx-1 h-4 w-px bg-[var(--color-line)]" />
        <button
          type="button"
          onClick={() => setCut((value) => !value)}
          className={`rounded-md border px-2.5 py-1 text-xs ${
            cut
              ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-white"
              : "border-[var(--color-line)] bg-white text-[var(--color-ink-faint)]"
          }`}
        >
          Section
        </button>
        <select
          value={shell}
          onChange={(event) => setShell(event.target.value as Shell)}
          className="rounded-md border border-[var(--color-line)] bg-white px-2 py-1 text-xs text-[var(--color-ink-faint)]"
          aria-label="Shell rendering"
        >
          {(Object.keys(SHELL_LABEL) as Shell[]).map((item) => (
            <option key={item} value={item}>
              {SHELL_LABEL[item]}
            </option>
          ))}
        </select>
        {airflow && (airflow.zones.length > 0 || airflow.couplings.length > 0) && (
          <button
            type="button"
            onClick={() => setShowAirflow((value) => !value)}
            className={`rounded-md border px-2.5 py-1 text-xs ${
              showAirflow
                ? "border-[var(--color-cold)] bg-[var(--color-cold)] text-white"
                : "border-[var(--color-line)] bg-white text-[var(--color-ink-faint)]"
            }`}
          >
            Air exchange
          </button>
        )}
        {geometry.layout && (
          <button
            type="button"
            onClick={() => setShowRooms((value) => !value)}
            className={`rounded-md border px-2.5 py-1 text-xs ${
              showRooms
                ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-white"
                : "border-[var(--color-line)] bg-white text-[var(--color-ink-faint)]"
            }`}
          >
            Rooms
          </button>
        )}
        {mode !== "construction" && (
          <select
            value={ramp}
            onChange={(event) => setRamp(event.target.value as RampName)}
            className="rounded-md border border-[var(--color-line)] bg-white px-2 py-1 text-xs text-[var(--color-ink-faint)]"
            aria-label="Colour scale"
          >
            {(Object.keys(RAMP_LABEL) as RampName[]).map((item) => (
              <option key={item} value={item}>
                {RAMP_LABEL[item]}
              </option>
            ))}
          </select>
        )}
        {hours.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setPlaying((value) => !value)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                playing
                  ? "border-[var(--color-solar)] bg-[var(--color-solar)] text-white"
                  : "border-[var(--color-line)] bg-white text-[var(--color-ink-faint)]"
              }`}
            >
              {playing ? "Pause" : "Play 24 h"}
            </button>
            <button
              type="button"
              onClick={jumpToNoon}
              className="rounded-md border border-[var(--color-line)] bg-white px-2.5 py-1 text-xs text-[var(--color-ink-faint)]"
            >
              Solar noon
            </button>
          </>
        )}
        <span className="ml-auto text-xs text-[var(--color-ink-faint)]">
          {engagedHint
            ? "drag to orbit, scroll to zoom, hover a face"
            : "click the view to take control, then drag and scroll"}
        </span>
      </div>

      <div className="relative">
        <div
          ref={mountRef}
          className="h-[440px] w-full overflow-hidden rounded-lg border border-[var(--color-line)]"
        />

        {hover && (
          <div
            className="pointer-events-none absolute z-10 rounded-md border border-[var(--color-line)] bg-white/95 px-2 py-1 text-[11px] shadow-sm"
            style={{
              left: Math.min(hover.x + 12, 620),
              top: Math.max(hover.y - 10, 4),
            }}
          >
            <div className="font-mono font-medium">{hover.name}</div>
            <div className="text-[var(--color-ink-faint)]">
              {num(hover.area, 2)} m²
              {painted && hoverValue !== undefined && hoverValue !== null
                ? ` · ${num(hoverValue)} ${unit}`
                : ""}
            </div>
          </div>
        )}

        {sun && (
          <div className="pointer-events-none absolute left-3 top-3 rounded-md border border-[var(--color-line)] bg-white/90 px-2.5 py-1.5 text-[11px] leading-relaxed">
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

      {geometry.layout && showRooms && rooms.length > 0 && (
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
            {geometry.layout.notes.length > 1 ? ` ${geometry.layout.notes[0]}` : ""}
          </p>
        </div>
      )}

      {showAirflow && airflow && (
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

      {painted && extent && (
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
        {painted
          ? "Every painted face is one number EnergyPlus reported for that surface at that hour, and the face names come from the simulation, so the value sits on the surface it belongs to."
          : "Material colours come from the Layer 0 database. The geometry drawn here is the geometry that was simulated, built from one description."}{" "}
        {terrain && terrain.positions.length > 0
          ? terrain.derived_from_horizon
            ? "The skyline is the terrain horizon table the simulation shades with, so what blocks the sun on screen is what blocked it in the run."
            : `The ground is the measured elevation model, ${terrain.vertex_count.toLocaleString()} points out to ${Math.round((terrain.outer_radius_m ?? 0) / 1000)} km, shaded by height above the site. It is the same ground the horizon profile the simulation shades with was measured from, and it is scaled uniformly so every skyline angle is the angle the run used. Heights are not exaggerated, and the colour is a display ramp over real elevation rather than observed snow.`
          : "No terrain is attached, so the simulation assumed a flat skyline in every direction."}{" "}
        There is no air temperature or velocity field here because the engine computes
        neither; those live in the detailed analysis tab and come from a fluid dynamics
        solver.
      </p>
    </div>
  );
}
