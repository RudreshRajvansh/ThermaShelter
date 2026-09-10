import * as THREE from 'three';
import { COLD_TO_HOT, rampRgb } from './format';

export interface ThermalFieldData {
  dims: [number, number, number]; // [nx, ny, nz]
  bounds_min: [number, number, number];
  bounds_max: [number, number, number];
  values: number[]; // flat array, x varies fastest
  min_c: number;
  max_c: number;
}

// Build a 1D transfer function texture from a color ramp
function buildTransferTexture(stops: [number, number, number][]): THREE.DataTexture {
  const width = 256;
  const data = new Uint8Array(width * 4);
  for (let i = 0; i < width; i++) {
    const t = i / (width - 1);
    const [r, g, b] = rampRgb(t, stops);
    data[i * 4] = Math.round(r);
    data[i * 4 + 1] = Math.round(g);
    data[i * 4 + 2] = Math.round(b);
    data[i * 4 + 3] = 180; // semi-transparent
  }
  const tex = new THREE.DataTexture(data, width, 1, THREE.RGBAFormat);
  tex.needsUpdate = true;
  return tex;
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
uniform vec3 uBoxMin;
uniform vec3 uBoxMax;
uniform float uClipY;  // for cross-section clipping

vec2 intersectBox(vec3 orig, vec3 dir) {
  vec3 tmin = (uBoxMin - orig) / dir;
  vec3 tmax = (uBoxMax - orig) / dir;
  vec3 t1 = min(tmin, tmax);
  vec3 t2 = max(tmin, tmax);
  float tNear = max(max(t1.x, t1.y), t1.z);
  float tFar = min(min(t2.x, t2.y), t2.z);
  return vec2(tNear, tFar);
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
    // Skip if above clip plane
    if (pos.z > uClipY) continue;
    // Normalize to [0,1] within the box
    vec3 uv = (pos - uBoxMin) / (uBoxMax - uBoxMin);
    float value = texture(uVolume, uv).r;
    vec4 color = texture(uTransfer, vec2(value, 0.5));
    color.a *= uOpacity * stepSize * 3.0;
    // Front-to-back compositing
    accum.rgb += (1.0 - accum.a) * color.rgb * color.a;
    accum.a += (1.0 - accum.a) * color.a;
    if (accum.a > 0.95) break;
  }
  
  gl_FragColor = accum;
}
`;

export function createThermalVolume(data: ThermalFieldData, stops: [number, number, number][] = COLD_TO_HOT) {
  const geom = new THREE.BoxGeometry(1, 1, 1);
  geom.translate(0.5, 0.5, 0.5);

  const tex = createData3DTexture(data);
  const transfer = buildTransferTexture(stops);

  const material = new THREE.ShaderMaterial({
    vertexShader,
    fragmentShader,
    uniforms: {
      uVolume: { value: tex },
      uTransfer: { value: transfer },
      uOpacity: { value: 0.6 },
      uBoxMin: { value: new THREE.Vector3(0, 0, 0) },
      uBoxMax: { value: new THREE.Vector3(1, 1, 1) },
      uClipY: { value: 1000.0 }, // Initialize to a large value, no clipping
      modelMatrixInverse: { value: new THREE.Matrix4() }
    },
    transparent: true,
    side: THREE.BackSide,
    depthWrite: false,
  });

  const mesh = new THREE.Mesh(geom, material);
  
  // Position and scale to match bounds
  const size = [
    data.bounds_max[0] - data.bounds_min[0],
    data.bounds_max[1] - data.bounds_min[1],
    data.bounds_max[2] - data.bounds_min[2]
  ];
  
  mesh.position.set(data.bounds_min[0], data.bounds_min[1], data.bounds_min[2]);
  mesh.scale.set(size[0], size[1], size[2]);

  mesh.onBeforeRender = (_renderer, _scene, _camera, _geometry, material, _group) => {
    (material as THREE.ShaderMaterial).uniforms.modelMatrixInverse.value.copy(mesh.matrixWorld).invert();
  };

  const update = (newData: ThermalFieldData) => {
    const newTex = createData3DTexture(newData);
    (material as THREE.ShaderMaterial).uniforms.uVolume.value.dispose();
    (material as THREE.ShaderMaterial).uniforms.uVolume.value = newTex;
  };

  const dispose = () => {
    geom.dispose();
    material.dispose();
    tex.dispose();
    transfer.dispose();
  };

  const setOpacity = (v: number) => {
    material.uniforms.uOpacity.value = v;
  };

  const setClipHeight = (h: number) => {
    material.uniforms.uClipY.value = h;
  };

  return { mesh, update, dispose, setOpacity, setClipHeight };
}

function createData3DTexture(data: ThermalFieldData): THREE.Data3DTexture {
  const [nx, ny, nz] = data.dims;
  const floatData = new Float32Array(nx * ny * nz);
  const range = data.max_c - data.min_c;
  
  for (let i = 0; i < data.values.length; i++) {
    floatData[i] = range > 0 ? (data.values[i] - data.min_c) / range : 0;
  }
  
  const tex = new THREE.Data3DTexture(floatData, nx, ny, nz);
  tex.format = THREE.RedFormat;
  tex.type = THREE.FloatType;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.unpackAlignment = 1;
  tex.needsUpdate = true;
  return tex;
}

export function probeTemperature(data: ThermalFieldData, point: THREE.Vector3): number | null {
  const [minX, minY, minZ] = data.bounds_min;
  const [maxX, maxY, maxZ] = data.bounds_max;
  
  if (
    point.x < minX || point.x > maxX ||
    point.y < minY || point.y > maxY ||
    point.z < minZ || point.z > maxZ
  ) {
    return null;
  }
  
  const [nx, ny, nz] = data.dims;
  
  // Normalize point to [0, 1] within bounds
  const px = (point.x - minX) / (maxX - minX);
  const py = (point.y - minY) / (maxY - minY);
  const pz = (point.z - minZ) / (maxZ - minZ);
  
  // Convert to grid coordinates
  const gx = px * (nx - 1);
  const gy = py * (ny - 1);
  const gz = pz * (nz - 1);
  
  const ix = Math.floor(gx);
  const iy = Math.floor(gy);
  const iz = Math.floor(gz);
  
  const fx = gx - ix;
  const fy = gy - iy;
  const fz = gz - iz;
  
  // Trilinear interpolation
  const getVal = (x: number, y: number, z: number) => {
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (z < 0) z = 0;
    if (x >= nx) x = nx - 1;
    if (y >= ny) y = ny - 1;
    if (z >= nz) z = nz - 1;
    return data.values[z * nx * ny + y * nx + x];
  };
  
  const c000 = getVal(ix, iy, iz);
  const c100 = getVal(ix + 1, iy, iz);
  const c010 = getVal(ix, iy + 1, iz);
  const c110 = getVal(ix + 1, iy + 1, iz);
  const c001 = getVal(ix, iy, iz + 1);
  const c101 = getVal(ix + 1, iy, iz + 1);
  const c011 = getVal(ix, iy + 1, iz + 1);
  const c111 = getVal(ix + 1, iy + 1, iz + 1);
  
  const c00 = c000 * (1 - fx) + c100 * fx;
  const c10 = c010 * (1 - fx) + c110 * fx;
  const c01 = c001 * (1 - fx) + c101 * fx;
  const c11 = c011 * (1 - fx) + c111 * fx;
  
  const c0 = c00 * (1 - fy) + c10 * fy;
  const c1 = c01 * (1 - fy) + c11 * fy;
  
  return c0 * (1 - fz) + c1 * fz;
}
