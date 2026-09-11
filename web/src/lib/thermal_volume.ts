import * as THREE from "three";
import { COLD_TO_HOT, rampRgb } from "./format";

export interface ThermalFieldData {
  dims: [number, number, number];
  bounds_min: [number, number, number];
  bounds_max: [number, number, number];
  values: (number | null)[];
  min_c: number;
  max_c: number;
}

const EMPTY = -1;

function buildTransferTexture(stops: [number, number, number][]): THREE.DataTexture {
  const width = 256;
  const data = new Uint8Array(width * 4);
  for (let i = 0; i < width; i++) {
    const [r, g, b] = rampRgb(i / (width - 1), stops);
    data[i * 4] = Math.round(r);
    data[i * 4 + 1] = Math.round(g);
    data[i * 4 + 2] = Math.round(b);
    data[i * 4 + 3] = 180;
  }
  const texture = new THREE.DataTexture(data, width, 1, THREE.RGBAFormat);
  texture.needsUpdate = true;
  return texture;
}

const vertexShader = `
varying vec3 vOrigin;
varying vec3 vDirection;
uniform mat4 modelMatrixInverse;

void main() {
  vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
  vOrigin = (modelMatrixInverse * vec4(cameraPosition, 1.0)).xyz;
  vDirection = position - vOrigin;
  gl_Position = projectionMatrix * mvPos;
}
`;

const fragmentShader = `
precision highp float;
precision highp sampler3D;

varying vec3 vOrigin;
varying vec3 vDirection;

uniform sampler3D uVolume;
uniform sampler2D uTransfer;
uniform float uOpacity;
uniform float uClipZ;

vec2 intersectBox(vec3 orig, vec3 dir) {
  vec3 tmin = (vec3(0.0) - orig) / dir;
  vec3 tmax = (vec3(1.0) - orig) / dir;
  vec3 t1 = min(tmin, tmax);
  vec3 t2 = max(tmin, tmax);
  return vec2(max(max(t1.x, t1.y), t1.z), min(min(t2.x, t2.y), t2.z));
}

void main() {
  vec3 dir = normalize(vDirection);
  vec2 bounds = intersectBox(vOrigin, dir);
  if (bounds.x > bounds.y) discard;
  bounds.x = max(bounds.x, 0.0);

  vec4 accum = vec4(0.0);
  float stepSize = 1.0 / 64.0;

  for (float t = bounds.x; t < bounds.y; t += stepSize) {
    vec3 pos = vOrigin + dir * t;
    if (pos.z > uClipZ) continue;
    float value = texture(uVolume, clamp(pos, 0.0, 1.0)).r;
    if (value < 0.0) continue;
    vec4 color = texture(uTransfer, vec2(value, 0.5));
    color.a *= uOpacity * stepSize * 3.0;
    accum.rgb += (1.0 - accum.a) * color.rgb * color.a;
    accum.a += (1.0 - accum.a) * color.a;
    if (accum.a > 0.95) break;
  }

  gl_FragColor = accum;
}
`;

function spacing(data: ThermalFieldData): [number, number, number] {
  return [0, 1, 2].map(
    (axis) => (data.bounds_max[axis] - data.bounds_min[axis]) / Math.max(1, data.dims[axis] - 1),
  ) as [number, number, number];
}

function createData3DTexture(data: ThermalFieldData): THREE.Data3DTexture {
  const [nx, ny, nz] = data.dims;
  const samples = new Float32Array(nx * ny * nz).fill(EMPTY);
  const range = data.max_c - data.min_c;
  const count = Math.min(data.values.length, samples.length);
  for (let index = 0; index < count; index++) {
    const value = data.values[index];
    if (value === null || !Number.isFinite(value)) continue;
    samples[index] = range > 0 ? (value - data.min_c) / range : 0.5;
  }
  const texture = new THREE.Data3DTexture(samples, nx, ny, nz);
  texture.format = THREE.RedFormat;
  texture.type = THREE.FloatType;
  texture.minFilter = THREE.NearestFilter;
  texture.magFilter = THREE.NearestFilter;
  texture.unpackAlignment = 1;
  texture.needsUpdate = true;
  return texture;
}

export function createThermalVolume(
  data: ThermalFieldData,
  stops: [number, number, number][] = COLD_TO_HOT,
) {
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  geometry.translate(0.5, 0.5, 0.5);
  const transfer = buildTransferTexture(stops);
  const material = new THREE.ShaderMaterial({
    vertexShader,
    fragmentShader,
    uniforms: {
      uVolume: { value: createData3DTexture(data) },
      uTransfer: { value: transfer },
      uOpacity: { value: 0.6 },
      uClipZ: { value: 1000.0 },
      modelMatrixInverse: { value: new THREE.Matrix4() },
    },
    transparent: true,
    side: THREE.BackSide,
    depthWrite: false,
  });
  const mesh = new THREE.Mesh(geometry, material);
  let current = data;

  const place = (field: ThermalFieldData) => {
    const step = spacing(field);
    mesh.position.set(
      field.bounds_min[0] - step[0] / 2,
      field.bounds_min[1] - step[1] / 2,
      field.bounds_min[2] - step[2] / 2,
    );
    mesh.scale.set(
      field.bounds_max[0] - field.bounds_min[0] + step[0],
      field.bounds_max[1] - field.bounds_min[1] + step[1],
      field.bounds_max[2] - field.bounds_min[2] + step[2],
    );
  };
  place(data);

  mesh.onBeforeRender = () => {
    material.uniforms.modelMatrixInverse.value.copy(mesh.matrixWorld).invert();
  };

  const update = (next: ThermalFieldData) => {
    material.uniforms.uVolume.value.dispose();
    material.uniforms.uVolume.value = createData3DTexture(next);
    current = next;
    place(next);
  };

  const dispose = () => {
    geometry.dispose();
    material.uniforms.uVolume.value.dispose();
    material.dispose();
    transfer.dispose();
  };

  const setOpacity = (value: number) => {
    material.uniforms.uOpacity.value = value;
  };

  const setClipHeight = (worldZ: number) => {
    const step = spacing(current);
    const low = current.bounds_min[2] - step[2] / 2;
    const size = current.bounds_max[2] - current.bounds_min[2] + step[2];
    material.uniforms.uClipZ.value = size > 0 ? (worldZ - low) / size : 1000.0;
  };

  return { mesh, update, dispose, setOpacity, setClipHeight };
}

export function probeTemperature(data: ThermalFieldData, point: THREE.Vector3): number | null {
  const step = spacing(data);
  const coordinates = [point.x, point.y, point.z];
  const indices: number[] = [];
  for (let axis = 0; axis < 3; axis++) {
    if (!(step[axis] > 0)) return null;
    const index = Math.round((coordinates[axis] - data.bounds_min[axis]) / step[axis]);
    if (index < 0 || index >= data.dims[axis]) return null;
    indices.push(index);
  }
  const [nx, ny] = data.dims;
  const value = data.values[indices[2] * nx * ny + indices[1] * nx + indices[0]];
  return value === null || value === undefined || !Number.isFinite(value) ? null : value;
}
