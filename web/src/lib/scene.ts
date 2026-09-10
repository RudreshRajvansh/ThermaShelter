import * as THREE from "three";
import type { AirflowSummary, LayoutPayload, SunPosition } from "./api";
import { ELEVATION, rampRgb } from "./format";

export const UP = new THREE.Vector3(0, 0, 1);

export function sunVector(
  altitudeDeg: number,
  azimuthDeg: number,
  radius: number,
): THREE.Vector3 {
  const altitude = THREE.MathUtils.degToRad(altitudeDeg);
  const azimuth = THREE.MathUtils.degToRad(azimuthDeg);
  return new THREE.Vector3(
    radius * Math.cos(altitude) * Math.sin(azimuth),
    radius * Math.cos(altitude) * Math.cos(azimuth),
    radius * Math.sin(altitude),
  );
}

const SKY_VERTEX = `
varying vec3 vWorld;
void main() {
  vWorld = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const SKY_FRAGMENT = `
varying vec3 vWorld;
uniform vec3 horizonColour;
uniform vec3 zenithColour;
uniform vec3 groundColour;
uniform vec3 sunDirection;
uniform float sunStrength;
void main() {
  vec3 direction = normalize(vWorld);
  float height = direction.z;
  vec3 sky = mix(horizonColour, zenithColour, clamp(pow(max(height, 0.0), 0.55), 0.0, 1.0));
  vec3 colour = height < 0.0 ? mix(horizonColour, groundColour, clamp(-height * 3.0, 0.0, 1.0)) : sky;
  float glow = pow(max(dot(direction, normalize(sunDirection)), 0.0), 26.0);
  colour += vec3(1.0, 0.86, 0.62) * glow * sunStrength;
  gl_FragColor = vec4(colour, 1.0);
}
`;

export function createSky(radius: number): {
  mesh: THREE.Mesh;
  setSun: (direction: THREE.Vector3, altitudeDeg: number) => void;
} {
  const uniforms = {
    horizonColour: { value: new THREE.Color("#cfd9e4") },
    zenithColour: { value: new THREE.Color("#5d87bd") },
    groundColour: { value: new THREE.Color("#8d8375") },
    sunDirection: { value: new THREE.Vector3(0, 0, 1) },
    sunStrength: { value: 1.0 },
  };
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 48, 32),
    new THREE.ShaderMaterial({
      uniforms,
      vertexShader: SKY_VERTEX,
      fragmentShader: SKY_FRAGMENT,
      side: THREE.BackSide,
      depthWrite: false,
    }),
  );
  mesh.frustumCulled = false;

  const day = { horizon: "#cfd9e4", zenith: "#5d87bd" };
  const dusk = { horizon: "#e0a878", zenith: "#3d5273" };
  const night = { horizon: "#141d2e", zenith: "#0f1626" };

  const setSun = (direction: THREE.Vector3, altitudeDeg: number) => {
    uniforms.sunDirection.value.copy(direction).normalize();
    const daylight = THREE.MathUtils.clamp(altitudeDeg / 18, 0, 1);
    const twilight = THREE.MathUtils.clamp((altitudeDeg + 8) / 14, 0, 1);
    const horizon = new THREE.Color(night.horizon)
      .lerp(new THREE.Color(dusk.horizon), twilight)
      .lerp(new THREE.Color(day.horizon), daylight);
    const zenith = new THREE.Color(night.zenith)
      .lerp(new THREE.Color(dusk.zenith), twilight)
      .lerp(new THREE.Color(day.zenith), daylight);
    uniforms.horizonColour.value.copy(horizon);
    uniforms.zenithColour.value.copy(zenith);
    uniforms.sunStrength.value = daylight;
  };
  setSun(new THREE.Vector3(0, 0, 1), 45);
  return { mesh, setSun };
}

export function createGround(radius: number): THREE.Mesh {
  const mesh = new THREE.Mesh(
    new THREE.CircleGeometry(radius, 96),
    new THREE.MeshStandardMaterial({
      color: new THREE.Color("#8f8264"),
      roughness: 0.96,
      metalness: 0.0,
    }),
  );
  mesh.receiveShadow = true;
  return mesh;
}

export function createTerrain(
  positions: number[],
  indices: number[],
  scale = 1,
  colourByElevation = false,
): THREE.Mesh | null {
  if (positions.length === 0 || indices.length === 0) return null;
  const geometry = new THREE.BufferGeometry();
  const scaled = scale === 1 ? positions : positions.map((value) => value * scale);
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(new Float32Array(scaled), 3),
  );
  geometry.setIndex(indices);
  geometry.computeVertexNormals();

  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color("#5d5641"),
    side: THREE.DoubleSide,
    flatShading: true,
    roughness: 0.98,
    metalness: 0.0,
  });

  if (colourByElevation) {
    let low = Infinity;
    let high = -Infinity;
    for (let i = 2; i < scaled.length; i += 3) {
      if (scaled[i] < low) low = scaled[i];
      if (scaled[i] > high) high = scaled[i];
    }
    const span = high - low;
    if (span > 1e-6) {
      const colours = new Float32Array((scaled.length / 3) * 3);
      for (let v = 0, i = 2; i < scaled.length; i += 3, v += 3) {
        const [r, g, b] = rampRgb((scaled[i] - low) / span, ELEVATION);
        colours[v] = r / 255;
        colours[v + 1] = g / 255;
        colours[v + 2] = b / 255;
      }
      geometry.setAttribute("color", new THREE.BufferAttribute(colours, 3));
      material.vertexColors = true;
      material.color.set("#ffffff");
    }
  }

  const mesh = new THREE.Mesh(geometry, material);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

const ROOM_TINTS = [
  "#c9a227", "#2f7f9e", "#8a6fae", "#4f8f5a",
  "#c06a3e", "#5a7d9a", "#a8556b", "#7a8b3f",
];

export function createLayout(
  layout: LayoutPayload,
  origin: { x: number; y: number },
): { group: THREE.Group; rooms: { name: string; colour: string; area: number }[] } {
  const group = new THREE.Group();
  const legend: { name: string; colour: string; area: number }[] = [];

  layout.rooms.forEach((room, index) => {
    const colour = ROOM_TINTS[index % ROOM_TINTS.length];
    legend.push({ name: room.name, colour, area: room.area_m2 });
    const width = room.x1 - room.x0;
    const depth = room.y1 - room.y0;
    if (width <= 0 || depth <= 0) return;
    const patch = new THREE.Mesh(
      new THREE.PlaneGeometry(width, depth),
      new THREE.MeshBasicMaterial({
        color: colour,
        transparent: true,
        opacity: room.heated ? 0.3 : 0.15,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    );
    patch.position.set(
      origin.x + (room.x0 + room.x1) / 2,
      origin.y + (room.y0 + room.y1) / 2,
      0.02,
    );
    patch.renderOrder = 2;
    group.add(patch);
  });

  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color("#c8bda8"),
    transparent: true,
    opacity: 0.9,
    roughness: 0.9,
    metalness: 0.02,
  });
  for (const run of layout.partitions) {
    const dx = run.x1 - run.x0;
    const dy = run.y1 - run.y0;
    const length = Math.hypot(dx, dy);
    if (length <= 0) continue;
    const wall = new THREE.Mesh(
      new THREE.BoxGeometry(length, 0.1, run.height_m),
      material,
    );
    wall.position.set(
      origin.x + (run.x0 + run.x1) / 2,
      origin.y + (run.y0 + run.y1) / 2,
      run.height_m / 2,
    );
    wall.rotation.z = Math.atan2(dy, dx);
    wall.castShadow = true;
    wall.receiveShadow = true;
    group.add(wall);
  }
  return { group, rooms: legend };
}

export function createSunPathArc(
  positions: SunPosition[],
  radius: number,
): THREE.Group {
  const group = new THREE.Group();
  const visible: THREE.Vector3[] = [];
  const blocked: THREE.Vector3[] = [];
  for (const item of positions) {
    if (!item.above_horizon) continue;
    const point = sunVector(item.altitude_deg, item.azimuth_deg, radius);
    (item.visible ? visible : blocked).push(point);
  }
  const build = (points: THREE.Vector3[], colour: string, thickness: number) => {
    if (points.length < 2) return;
    const curve = new THREE.CatmullRomCurve3(points);
    const geometry = new THREE.TubeGeometry(
      curve,
      Math.max(16, points.length * 2),
      thickness,
      8,
      false,
    );
    group.add(
      new THREE.Mesh(
        geometry,
        new THREE.MeshBasicMaterial({ color: colour, toneMapped: false }),
      ),
    );
  };
  const thickness = radius * 0.006;
  build(blocked, "#9b6a44", thickness * 0.7);
  build(visible, "#ffc043", thickness);
  return group;
}

export function createCompass(radius: number): THREE.Group {
  const group = new THREE.Group();
  const marks: [string, number, string][] = [
    ["N", 0, "#c53030"],
    ["E", 90, "#5b6672"],
    ["S", 180, "#2b6cb0"],
    ["W", 270, "#5b6672"],
  ];
  for (const [, azimuth, colour] of marks) {
    const angle = THREE.MathUtils.degToRad(azimuth);
    const inner = new THREE.Vector3(
      radius * 0.86 * Math.sin(angle),
      radius * 0.86 * Math.cos(angle),
      0.05,
    );
    const outer = new THREE.Vector3(
      radius * Math.sin(angle),
      radius * Math.cos(angle),
      0.05,
    );
    const geometry = new THREE.BufferGeometry().setFromPoints([inner, outer]);
    group.add(
      new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: colour })),
    );
  }
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(radius * 0.985, radius, 128),
    new THREE.MeshBasicMaterial({
      color: "#5b6672",
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.45,
    }),
  );
  ring.position.z = 0.04;
  group.add(ring);
  return group;
}

export function createSunDisc(radius: number): {
  group: THREE.Group;
  setState: (shining: boolean) => void;
} {
  const group = new THREE.Group();
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 24, 18),
    new THREE.MeshBasicMaterial({ color: "#ffd166", toneMapped: false }),
  );
  group.add(core);

  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const context = canvas.getContext("2d")!;
  const gradient = context.createRadialGradient(64, 64, 4, 64, 64, 64);
  gradient.addColorStop(0, "rgba(255,220,150,0.95)");
  gradient.addColorStop(0.35, "rgba(255,196,90,0.35)");
  gradient.addColorStop(1, "rgba(255,196,90,0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 128, 128);
  const halo = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(canvas),
      transparent: true,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  halo.scale.setScalar(radius * 9);
  group.add(halo);

  const setState = (shining: boolean) => {
    (core.material as THREE.MeshBasicMaterial).color.set(
      shining ? "#ffd166" : "#8e97a1",
    );
    halo.visible = shining;
  };
  return { group, setState };
}

export function makeLabel(text: string, colour = "#12161c"): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 128;
  const context = canvas.getContext("2d")!;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.font = "bold 76px system-ui, sans-serif";
  context.fillStyle = colour;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text, canvas.width / 2, canvas.height / 2);
  const texture = new THREE.CanvasTexture(canvas);
  texture.anisotropy = 4;
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.premultiplyAlpha = false;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      alphaTest: 0.02,
      toneMapped: false,
    }),
  );
  sprite.scale.set(8, 4, 1);
  return sprite;
}

export function disposeObject(root: THREE.Object3D): void {
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    const material = (mesh as THREE.Mesh).material;
    if (!material) return;
    if (Array.isArray(material)) material.forEach((item) => item.dispose());
    else material.dispose();
  });
}

export function createAirflow(
  airflow: AirflowSummary,
  layout: LayoutPayload | null | undefined,
  origin: { x: number; y: number },
  height: number,
): { group: THREE.Group; legend: { label: string; detail: string }[] } {
  const group = new THREE.Group();
  const legend: { label: string; detail: string }[] = [];

  const peak = Math.max(
    ...airflow.zones.map((zone) => zone.infiltration_m3_s_mean),
    ...airflow.couplings.map((pair) => pair.volume_flow_m3_s_mean),
    1e-6,
  );

  const anchor = (name: string): THREE.Vector3 => {
    const room = layout?.rooms.find(
      (item) => item.name.toUpperCase().replace(/\s+/g, "_") === name,
    );
    if (room) {
      return new THREE.Vector3(
        origin.x + (room.x0 + room.x1) / 2,
        origin.y + (room.y0 + room.y1) / 2,
        height * 0.55,
      );
    }
    return new THREE.Vector3(origin.x, origin.y, height * 0.55);
  };

  for (const zone of airflow.zones) {
    if (zone.infiltration_m3_s_mean <= 0) continue;
    const centre = anchor(zone.zone);
    const scale = 0.35 + 0.65 * (zone.infiltration_m3_s_mean / peak);
    const count = 8;
    for (let i = 0; i < count; i++) {
      const angle = (i / count) * Math.PI * 2;
      const reach = height * 1.5 * scale;
      const from = new THREE.Vector3(
        centre.x + Math.cos(angle) * reach,
        centre.y + Math.sin(angle) * reach,
        centre.z + height * 0.25,
      );
      const arrow = new THREE.ArrowHelper(
        new THREE.Vector3().subVectors(centre, from).normalize(),
        from,
        from.distanceTo(centre) * 0.55,
        0x4aa3df,
        height * 0.16 * scale,
        height * 0.1 * scale,
      );
      group.add(arrow);
    }
    legend.push({
      label: `${zone.zone.toLowerCase().replace(/_/g, " ")} leakage`,
      detail:
        `${zone.infiltration_ach_mean.toFixed(2)} ACH mean, ` +
        `${(zone.infiltration_m3_s_mean * 3600).toFixed(0)} m³/h through the envelope`,
    });
  }

  for (const pair of airflow.couplings) {
    if (pair.volume_flow_m3_s_mean <= 0) continue;
    const from = anchor(pair.from_zone);
    const to = anchor(pair.to_zone);
    const scale = 0.4 + 0.6 * (pair.volume_flow_m3_s_mean / peak);
    const direction = new THREE.Vector3().subVectors(to, from);
    const length = direction.length();
    if (length <= 1e-6) continue;
    group.add(
      new THREE.ArrowHelper(
        direction.clone().normalize(),
        from,
        length,
        0xe08a3c,
        height * 0.22 * scale,
        height * 0.14 * scale,
      ),
    );
    legend.push({
      label: `${pair.from_zone.toLowerCase()} to ${pair.to_zone.toLowerCase()}`,
      detail:
        `${(pair.volume_flow_m3_s_mean * 3600).toFixed(0)} m³/h mean, ` +
        `${(pair.volume_flow_m3_s_peak * 3600).toFixed(0)} m³/h peak`,
    });
  }

  return { group, legend };
}
