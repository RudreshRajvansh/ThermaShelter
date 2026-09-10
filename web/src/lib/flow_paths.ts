import * as THREE from "three";
import type { AirflowSummary, LayoutPayload } from "./api";

const PARTICLE_VERTEX = `
uniform float uTime;
attribute float progressOffset;
attribute vec3 customColor;
attribute vec3 p0;
attribute vec3 p1;
attribute vec3 p2;

varying vec3 vColor;
varying float vAlpha;

void main() {
  vColor = customColor;
  
  // Particles move from 0 to 1 along their curve
  // We use uTime + progressOffset to stagger them
  float t = fract(uTime * 0.5 + progressOffset);
  
  // Fade out at ends
  vAlpha = smoothstep(0.0, 0.2, t) * smoothstep(1.0, 0.8, t);
  
  // Quadratic Bezier interpolation
  float u = 1.0 - t;
  vec3 pos = u * u * p0 + 2.0 * u * t * p1 + t * t * p2;
  
  vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
  // Scale particles by distance
  gl_PointSize = 15.0 * (10.0 / -mvPosition.z);
  gl_Position = projectionMatrix * mvPosition;
}
`;

const PARTICLE_FRAGMENT = `
varying vec3 vColor;
varying float vAlpha;

void main() {
  // Soft circle
  float dist = length(gl_PointCoord - vec2(0.5));
  if (dist > 0.5) discard;
  
  float alpha = (0.5 - dist) * 2.0;
  
  gl_FragColor = vec4(vColor, alpha * vAlpha);
}
`;

export function createParticleAirflow(
  airflow: AirflowSummary,
  layout: LayoutPayload | null | undefined,
  origin: { x: number; y: number },
  height: number,
): {
  group: THREE.Group;
  legend: { label: string; detail: string }[];
  tick: (delta: number) => void;
  dispose: () => void;
} {
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

  const curves: { p0: THREE.Vector3; p1: THREE.Vector3; p2: THREE.Vector3; density: number; color: THREE.Color }[] = [];

  for (const zone of airflow.zones) {
    if (zone.infiltration_m3_s_mean <= 0) continue;
    const centre = anchor(zone.zone);
    const scale = 0.35 + 0.65 * (zone.infiltration_m3_s_mean / peak);
    const count = 8;
    const color = new THREE.Color(0x4aa3df);
    
    for (let i = 0; i < count; i++) {
      const angle = (i / count) * Math.PI * 2;
      const reach = height * 1.5 * scale;
      const from = new THREE.Vector3(
        centre.x + Math.cos(angle) * reach,
        centre.y + Math.sin(angle) * reach,
        centre.z + height * 0.25,
      );
      
      const mid = new THREE.Vector3().lerpVectors(from, centre, 0.5);
      mid.z += height * 0.2; // slight arc
      
      curves.push({ p0: from, p1: mid, p2: centre, density: Math.max(5, Math.floor(10 * scale)), color });
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
    
    const color = new THREE.Color(0xe08a3c);
    
    const mid = new THREE.Vector3().lerpVectors(from, to, 0.5);
    mid.z += height * 0.3; // arc up
    
    curves.push({ p0: from, p1: mid, p2: to, density: Math.max(5, Math.floor(20 * scale)), color });
    
    legend.push({
      label: `${pair.from_zone.toLowerCase()} to ${pair.to_zone.toLowerCase()}`,
      detail:
        `${(pair.volume_flow_m3_s_mean * 3600).toFixed(0)} m³/h mean, ` +
        `${(pair.volume_flow_m3_s_peak * 3600).toFixed(0)} m³/h peak`,
    });
  }

  // Generate particle data
  const positions: number[] = [];
  const p0Array: number[] = [];
  const p1Array: number[] = [];
  const p2Array: number[] = [];
  const progressOffsets: number[] = [];
  const customColors: number[] = [];
  
  for (const curve of curves) {
    const { p0, p1, p2, density, color } = curve;
    
    for (let p = 0; p < density; p++) {
      const t = p / density;
      
      // We don't use the static position attribute for rendering, but Three.js might need it for frustum culling.
      // We'll just set it to p1 for bounding box purposes.
      positions.push(p1.x, p1.y, p1.z);
      
      p0Array.push(p0.x, p0.y, p0.z);
      p1Array.push(p1.x, p1.y, p1.z);
      p2Array.push(p2.x, p2.y, p2.z);
      
      progressOffsets.push(t);
      customColors.push(color.r, color.g, color.b);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('p0', new THREE.Float32BufferAttribute(p0Array, 3));
  geometry.setAttribute('p1', new THREE.Float32BufferAttribute(p1Array, 3));
  geometry.setAttribute('p2', new THREE.Float32BufferAttribute(p2Array, 3));
  geometry.setAttribute('progressOffset', new THREE.Float32BufferAttribute(progressOffsets, 1));
  geometry.setAttribute('customColor', new THREE.Float32BufferAttribute(customColors, 3));

  const uniforms = {
    uTime: { value: 0 },
  };

  const material = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: PARTICLE_VERTEX,
    fragmentShader: PARTICLE_FRAGMENT,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const points = new THREE.Points(geometry, material);
  // Disable frustum culling since our points move outside their initial positions
  points.frustumCulled = false;
  group.add(points);

  const tick = (delta: number) => {
    uniforms.uTime.value += delta;
  };

  const dispose = () => {
    geometry.dispose();
    material.dispose();
  };

  return { group, legend, tick, dispose };
}
