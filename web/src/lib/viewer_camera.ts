import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

export type ViewName = "iso" | "plan" | "south" | "east" | "sun";

export const VIEW_LABEL: Record<ViewName, string> = {
  iso: "Isometric",
  plan: "Plan",
  south: "South elevation",
  east: "East elevation",
  sun: "From the sun",
};

export const VIEW_HINT: Record<ViewName, string> = {
  iso: "Three quarter view from the south east.",
  plan: "Straight down, north up.",
  south: "Square on to the south face, the face that collects.",
  east: "Square on to the east face.",
  sun: "Looking along the sun rays at this hour, so every lit face is a face the sun reaches.",
};

type Orbit = { theta: number; phi: number; distance: number };

const TRANSITION_MS = 620;

function ease(t: number): number {
  return 1 - Math.pow(1 - t, 4);
}

function shortestAngle(from: number, to: number): number {
  return from + Math.atan2(Math.sin(to - from), Math.cos(to - from));
}

export function orbitFor(view: ViewName, sun: { altitude: number; azimuth: number } | null):
  { theta: number; phi: number } {
  if (view === "plan") return { theta: Math.PI, phi: 0.045 };
  if (view === "south") return { theta: Math.PI, phi: Math.PI / 2 - 0.035 };
  if (view === "east") return { theta: Math.PI / 2, phi: Math.PI / 2 - 0.035 };
  if (view === "sun" && sun) {
    return {
      theta: THREE.MathUtils.degToRad(sun.azimuth),
      phi: THREE.MathUtils.degToRad(90 - Math.max(sun.altitude, 4)),
    };
  }
  return { theta: Math.PI * 0.78, phi: Math.PI * 0.38 };
}

export type CameraRig = {
  controls: OrbitControls;
  state: () => Orbit;
  restore: (orbit: Orbit) => void;
  goTo: (orbit: { theta: number; phi: number; distance?: number }) => void;
  tick: (delta: number) => boolean;
  setZoomEnabled: (enabled: boolean) => void;
  dispose: () => void;
};

export function createCameraRig(
  camera: THREE.PerspectiveCamera,
  element: HTMLElement,
  target: THREE.Vector3,
  bounds: { min: number; max: number },
  onChange: () => void,
): CameraRig {
  const controls = new OrbitControls(camera, element);
  controls.target.copy(target);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.rotateSpeed = 0.85;
  controls.panSpeed = 0.8;
  controls.zoomSpeed = 0.9;
  controls.zoomToCursor = true;
  controls.screenSpacePanning = true;
  controls.minDistance = bounds.min;
  controls.maxDistance = bounds.max;
  controls.maxPolarAngle = Math.PI / 2 - 0.02;
  controls.minPolarAngle = 0.03;
  controls.keyPanSpeed = 14;
  controls.enableZoom = false;
  controls.addEventListener("change", onChange);

  const offset = new THREE.Vector3();

  const read = (): Orbit => {
    offset.copy(camera.position).sub(controls.target);
    const distance = Math.max(offset.length(), 1e-6);
    return {
      theta: Math.atan2(offset.x, offset.y),
      phi: Math.acos(THREE.MathUtils.clamp(offset.z / distance, -1, 1)),
      distance,
    };
  };

  const write = (orbit: Orbit) => {
    const phi = THREE.MathUtils.clamp(orbit.phi, controls.minPolarAngle,
                                      controls.maxPolarAngle);
    const distance = THREE.MathUtils.clamp(orbit.distance, bounds.min, bounds.max);
    camera.position.set(
      controls.target.x + distance * Math.sin(phi) * Math.sin(orbit.theta),
      controls.target.y + distance * Math.sin(phi) * Math.cos(orbit.theta),
      controls.target.z + distance * Math.cos(phi),
    );
    camera.lookAt(controls.target);
  };

  let animation: { from: Orbit; to: Orbit; elapsed: number } | null = null;

  return {
    controls,
    state: read,
    restore: (orbit: Orbit) => {
      write(orbit);
      controls.update();
    },
    goTo: (next) => {
      const from = read();
      animation = {
        from,
        to: {
          theta: shortestAngle(from.theta, next.theta),
          phi: next.phi,
          distance: next.distance ?? from.distance,
        },
        elapsed: 0,
      };
    },
    tick: (delta: number) => {
      let busy = false;
      if (animation) {
        animation.elapsed += delta * 1000;
        const t = Math.min(1, animation.elapsed / TRANSITION_MS);
        const k = ease(t);
        write({
          theta: THREE.MathUtils.lerp(animation.from.theta, animation.to.theta, k),
          phi: THREE.MathUtils.lerp(animation.from.phi, animation.to.phi, k),
          distance: THREE.MathUtils.lerp(animation.from.distance,
                                         animation.to.distance, k),
        });
        if (t >= 1) animation = null;
        busy = true;
      }
      return controls.update() || busy;
    },
    setZoomEnabled: (enabled: boolean) => {
      controls.enableZoom = enabled;
    },
    dispose: () => {
      controls.removeEventListener("change", onChange);
      controls.dispose();
    },
  };
}
