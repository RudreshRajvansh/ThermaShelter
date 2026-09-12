import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { GTAOPass } from "three/examples/jsm/postprocessing/GTAOPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { SMAAPass } from "three/examples/jsm/postprocessing/SMAAPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { Sky } from "three/examples/jsm/objects/Sky.js";

export type Quality = "high" | "balanced" | "plain";

export function detectQuality(): Quality {
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl2") as WebGL2RenderingContext | null;
  if (!gl) return "plain";
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const name = debug
    ? String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) ?? "")
    : "";
  const size = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number;
  const cores = navigator.hardwareConcurrency ?? 4;
  const software = /swiftshader|llvmpipe|software|basic render/i.test(name);
  if (software || size < 8192 || cores <= 2) return "plain";
  const integrated = /intel|uhd graphics|hd graphics|apple m1|mali|adreno/i.test(name);
  return integrated && cores <= 8 ? "balanced" : "high";
}

export function createSkyDome(radius: number, intensity = 1): {
  mesh: THREE.Mesh;
  setSun: (direction: THREE.Vector3, altitudeDeg: number) => void;
  uniforms: Record<string, THREE.IUniform>;
} {
  const sky = new Sky();
  sky.scale.setScalar(Math.max(radius * 4, 4000));
  sky.frustumCulled = false;
  const material = sky.material as THREE.ShaderMaterial;
  material.uniforms.skyIntensity = { value: intensity };
  material.fragmentShader = material.fragmentShader
    .replace("uniform vec3 up;", "uniform vec3 up; uniform float skyIntensity;")
    .replace("gl_FragColor = vec4( retColor, 1.0 );",
             "gl_FragColor = vec4( retColor * skyIntensity, 1.0 );");
  const uniforms = sky.material.uniforms as Record<string, THREE.IUniform>;
  uniforms.up.value.set(0, 0, 1);
  uniforms.turbidity.value = 2.2;
  uniforms.rayleigh.value = 1.4;
  uniforms.mieCoefficient.value = 0.004;
  uniforms.mieDirectionalG.value = 0.82;

  const setSun = (direction: THREE.Vector3, altitudeDeg: number) => {
    uniforms.sunPosition.value.copy(direction).normalize();
    const daylight = THREE.MathUtils.clamp((altitudeDeg + 4) / 20, 0, 1);
    uniforms.turbidity.value = THREE.MathUtils.lerp(6.5, 2.0, daylight);
    uniforms.rayleigh.value = THREE.MathUtils.lerp(3.2, 1.15, daylight);
    uniforms.mieCoefficient.value = THREE.MathUtils.lerp(0.011, 0.0035, daylight);
  };

  return { mesh: sky as unknown as THREE.Mesh, setSun, uniforms };
}

export type SkyEnvironment = {
  refresh: (direction: THREE.Vector3, altitudeDeg: number) => THREE.Texture;
  texture: () => THREE.Texture | null;
  dispose: () => void;
};

export function createSkyEnvironment(renderer: THREE.WebGLRenderer): SkyEnvironment {
  const generator = new THREE.PMREMGenerator(renderer);
  generator.compileEquirectangularShader();
  const scene = new THREE.Scene();
  const sky = createSkyDome(1000);
  scene.add(sky.mesh);
  let target: THREE.WebGLRenderTarget | null = null;

  return {
    refresh: (direction: THREE.Vector3, altitudeDeg: number) => {
      sky.setSun(direction, altitudeDeg);
      const next = generator.fromScene(scene);
      target?.dispose();
      target = next;
      return next.texture;
    },
    texture: () => target?.texture ?? null,
    dispose: () => {
      target?.dispose();
      target = null;
      sky.mesh.geometry.dispose();
      (sky.mesh.material as THREE.Material).dispose();
      generator.dispose();
    },
  };
}

export type Pipeline = {
  render: () => void;
  resize: (width: number, height: number) => void;
  setQuality: (quality: Quality) => void;
  quality: () => Quality;
  dispose: () => void;
};

export function createPipeline(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  camera: THREE.PerspectiveCamera,
  width: number,
  height: number,
  requested: Quality,
): Pipeline {
  let quality = requested;
  let composer: EffectComposer | null = null;
  let gtao: GTAOPass | null = null;
  let bloom: UnrealBloomPass | null = null;
  let smaa: SMAAPass | null = null;
  let size = new THREE.Vector2(width, height);

  const teardown = () => {
    gtao?.dispose?.();
    bloom?.dispose?.();
    smaa?.dispose?.();
    composer?.dispose?.();
    composer = null;
    gtao = null;
    bloom = null;
    smaa = null;
  };

  const build = () => {
    teardown();
    if (quality === "plain") return;

    const buffer = new THREE.WebGLRenderTarget(size.x, size.y, {
      type: THREE.HalfFloatType,
      depthBuffer: true,
      stencilBuffer: true,
    });
    const next = new EffectComposer(renderer, buffer);
    next.setSize(size.x, size.y);
    next.addPass(new RenderPass(scene, camera));

    const ao = new GTAOPass(scene, camera, size.x, size.y);
    ao.output = GTAOPass.OUTPUT.Default;
    ao.updateGtaoMaterial({
      radius: quality === "high" ? 0.5 : 0.35,
      distanceExponent: 1.2,
      thickness: 1.0,
      scale: 1.0,
      samples: quality === "high" ? 24 : 10,
      screenSpaceRadius: false,
    });
    ao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 4, rings: 2,
                          samples: quality === "high" ? 16 : 8 });
    next.addPass(ao);
    gtao = ao;

    const glow = new UnrealBloomPass(size.clone(), 0.22, 0.7, 0.94);
    next.addPass(glow);
    bloom = glow;

    if (quality === "high") {
      const edge = new SMAAPass(size.x, size.y);
      next.addPass(edge);
      smaa = edge;
    }

    next.addPass(new OutputPass());
    composer = next;
  };

  build();

  return {
    render: () => {
      if (composer) composer.render();
      else renderer.render(scene, camera);
    },
    resize: (nextWidth: number, nextHeight: number) => {
      size = new THREE.Vector2(nextWidth, nextHeight);
      renderer.setSize(nextWidth, nextHeight);
      composer?.setSize(nextWidth, nextHeight);
      gtao?.setSize(nextWidth, nextHeight);
      bloom?.setSize(nextWidth, nextHeight);
      smaa?.setSize(nextWidth, nextHeight);
    },
    setQuality: (next: Quality) => {
      if (next === quality) return;
      quality = next;
      build();
    },
    quality: () => quality,
    dispose: teardown,
  };
}

function noiseCanvas(size: number, draw: (data: ImageData) => void): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  if (!context) return canvas;
  const image = context.createImageData(size, size);
  draw(image);
  context.putImageData(image, 0, 0);
  return canvas;
}

function hash(x: number, y: number, seed: number): number {
  const value = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453;
  return value - Math.floor(value);
}

function fbm(x: number, y: number, seed: number, octaves: number): number {
  let total = 0;
  let amplitude = 1;
  let frequency = 1;
  let norm = 0;
  for (let i = 0; i < octaves; i++) {
    const sx = x * frequency;
    const sy = y * frequency;
    const ix = Math.floor(sx);
    const iy = Math.floor(sy);
    const fx = sx - ix;
    const fy = sy - iy;
    const a = hash(ix, iy, seed);
    const b = hash(ix + 1, iy, seed);
    const c = hash(ix, iy + 1, seed);
    const d = hash(ix + 1, iy + 1, seed);
    const ux = fx * fx * (3 - 2 * fx);
    const uy = fy * fy * (3 - 2 * fy);
    total += amplitude * (a * (1 - ux) * (1 - uy) + b * ux * (1 - uy) +
                          c * (1 - ux) * uy + d * ux * uy);
    norm += amplitude;
    amplitude *= 0.5;
    frequency *= 2;
  }
  return total / norm;
}

const cache = new Map<string, { normal: THREE.Texture; rough: THREE.Texture }>();

export function detailMaps(
  finish: string,
  strength = 1,
): { normal: THREE.Texture; rough: THREE.Texture } {
  const key = `${finish}:${strength.toFixed(2)}`;
  const existing = cache.get(key);
  if (existing) return existing;

  const size = 256;
  const grain = finish === "rough" ? 26 : finish === "smooth" ? 7 : 3;
  const scale = finish === "rough" ? 9 : 5;

  const heightAt = (x: number, y: number) =>
    fbm((x / size) * scale, (y / size) * scale, 3, finish === "rough" ? 4 : 3);

  const normalCanvas = noiseCanvas(size, (image) => {
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const left = heightAt((x - 1 + size) % size, y);
        const right = heightAt((x + 1) % size, y);
        const up = heightAt(x, (y - 1 + size) % size);
        const down = heightAt(x, (y + 1) % size);
        const dx = (left - right) * grain * strength;
        const dy = (up - down) * grain * strength;
        const length = Math.hypot(dx, dy, 1);
        const index = (y * size + x) * 4;
        image.data[index] = ((dx / length) * 0.5 + 0.5) * 255;
        image.data[index + 1] = ((dy / length) * 0.5 + 0.5) * 255;
        image.data[index + 2] = (1 / length) * 255;
        image.data[index + 3] = 255;
      }
    }
  });

  const roughCanvas = noiseCanvas(size, (image) => {
    const spread = finish === "rough" ? 60 : 26;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const value = heightAt(x, y);
        const level = Math.max(0, Math.min(255, 190 + (value - 0.5) * spread));
        const index = (y * size + x) * 4;
        image.data[index] = level;
        image.data[index + 1] = level;
        image.data[index + 2] = level;
        image.data[index + 3] = 255;
      }
    }
  });

  const build = (canvas: HTMLCanvasElement) => {
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;
    texture.anisotropy = 4;
    return texture;
  };

  const maps = { normal: build(normalCanvas), rough: build(roughCanvas) };
  cache.set(key, maps);
  return maps;
}
